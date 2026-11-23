package domain

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"math"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

type LogAttribute struct {
	Type  string `json:"type"`
	Value string `json:"value"`
}
type LogRule struct {
	ID         string   `json:"id"`
	Name       string   `json:"name"`
	Kind       string   `json:"kind"`
	Enabled    bool     `json:"enabled"`
	Attribute  string   `json:"attribute,omitempty"`
	Values     []string `json:"values,omitempty"`
	Percentage float64  `json:"percentage"`
}
type LogPolicy struct {
	Version int64     `json:"version"`
	Rules   []LogRule `json:"rules"`
}
type LogMatch struct {
	RuleID    string `json:"ruleId"`
	MatchedBy string `json:"matchedBy"`
}
type LogDecision struct {
	Allowed       bool       `json:"allowed"`
	MatchedRules  []LogMatch `json:"matchedRules"`
	PolicyVersion int64      `json:"policyVersion"`
}

func logText(s string, max int, required bool) bool {
	return (!required || strings.TrimSpace(s) != "") && len(s) <= max && utf8.ValidString(s) && !strings.ContainsRune(s, 0)
}
func ValidateLogAttributes(attrs []LogAttribute) error {
	if len(attrs) > 3 {
		return ErrInvalid
	}
	seen := map[string]bool{}
	for _, a := range attrs {
		if !slices.Contains([]string{"device", "user", "model"}, a.Type) || seen[a.Type] || !logText(a.Value, 256, true) {
			return ErrInvalid
		}
		seen[a.Type] = true
	}
	return nil
}
func (p LogPolicy) Validate() error {
	if p.Version < 0 || len(p.Rules) > 100 {
		return ErrInvalid
	}
	ids := map[string]bool{}
	for _, r := range p.Rules {
		if !logText(r.ID, 80, true) || ids[r.ID] || !logText(r.Name, 160, true) {
			return ErrInvalid
		}
		ids[r.ID] = true
		switch r.Kind {
		case "specified":
			if !slices.Contains([]string{"device", "user", "model"}, r.Attribute) || len(r.Values) == 0 || len(r.Values) > 500 || r.Percentage != 0 {
				return ErrInvalid
			}
			seen := map[string]bool{}
			for _, v := range r.Values {
				if !logText(v, 256, true) || seen[v] {
					return ErrInvalid
				}
				seen[v] = true
			}
		case "percentage":
			if r.Attribute != "" || len(r.Values) > 0 || math.IsNaN(r.Percentage) || math.IsInf(r.Percentage, 0) || r.Percentage < 0 || r.Percentage > 100 {
				return ErrInvalid
			}
		default:
			return ErrInvalid
		}
	}
	return nil
}

// Project/environment salt is independent of rule IDs, so rollout expansion is monotonic.
func (p LogPolicy) Decide(project, env string, attrs []LogAttribute) LogDecision {
	d := LogDecision{PolicyVersion: p.Version, MatchedRules: []LogMatch{}}
	a := map[string]string{}
	for _, v := range attrs {
		a[v.Type] = v.Value
	}
	for _, r := range p.Rules {
		if !r.Enabled {
			continue
		}
		hit := false
		if r.Kind == "specified" {
			hit = a[r.Attribute] != "" && slices.Contains(r.Values, a[r.Attribute])
		} else if r.Kind == "percentage" && a["device"] != "" {
			sum := sha256.Sum256([]byte(project + "\x00" + env + "\x00" + a["device"]))
			bucket := binary.BigEndian.Uint32(sum[:4]) % 10000
			hit = float64(bucket) < r.Percentage*100
		}
		if hit {
			d.MatchedRules = append(d.MatchedRules, LogMatch{r.ID, r.Kind})
			d.Allowed = true
		}
	}
	return d
}

type ApplicationLog struct {
	SchemaVersion int             `json:"schema_version"`
	EventID       string          `json:"event_id"`
	OccurredAt    time.Time       `json:"occurred_at"`
	ReceivedAt    time.Time       `json:"received_at,omitempty"`
	Level         string          `json:"level"`
	Source        string          `json:"source"`
	Event         string          `json:"event"`
	Message       string          `json:"message"`
	UserID        string          `json:"user_id,omitempty"`
	DeviceID      string          `json:"device_id,omitempty"`
	Model         string          `json:"model,omitempty"`
	AppVersion    string          `json:"app_version,omitempty"`
	RequestID     string          `json:"request_id,omitempty"`
	TraceID       string          `json:"trace_id,omitempty"`
	Data          json.RawMessage `json:"data,omitempty"`
}

func (e ApplicationLog) Attributes() []LogAttribute {
	a := []LogAttribute{}
	for _, v := range []LogAttribute{{"user", e.UserID}, {"device", e.DeviceID}, {"model", e.Model}} {
		if v.Value != "" {
			a = append(a, v)
		}
	}
	return a
}
func (e ApplicationLog) Validate() error {
	if e.SchemaVersion != 1 || !logText(e.EventID, 128, true) || e.OccurredAt.IsZero() || e.OccurredAt.Year() < 1 || e.OccurredAt.After(time.Now().Add(5*time.Minute)) || !slices.Contains([]string{"DEBUG", "INFO", "WARN", "ERROR"}, e.Level) || !logText(e.Source, 80, true) || !logText(e.Event, 160, true) || !logText(e.Message, 4096, true) {
		return ErrInvalid
	}
	for _, v := range []string{e.UserID, e.DeviceID, e.Model, e.AppVersion, e.RequestID, e.TraceID} {
		if !logText(v, 256, false) {
			return ErrInvalid
		}
	}
	if len(e.Data) > 16384 {
		return ErrInvalid
	}
	if len(e.Data) > 0 {
		if !validLogJSON(e.Data) {
			return ErrInvalid
		}
		var obj map[string]json.RawMessage
		if json.Unmarshal(e.Data, &obj) != nil || obj == nil {
			return ErrInvalid
		}
	}
	return nil
}

// Decode into JSON-safe scalar values before handing data to PostgreSQL JSONB.
// Reject NUL and unpaired Unicode surrogate escapes instead of returning a DB 500.
func validLogJSON(raw json.RawMessage) bool {
	var value any
	if json.Unmarshal(raw, &value) != nil {
		return false
	} // also rejects numbers outside finite float64
	var walk func(any) bool
	walk = func(v any) bool {
		switch x := v.(type) {
		case string:
			return !strings.ContainsRune(x, 0)
		case []any:
			for _, i := range x {
				if !walk(i) {
					return false
				}
			}
		case map[string]any:
			for k, i := range x {
				if strings.ContainsRune(k, 0) || !walk(i) {
					return false
				}
			}
		}
		return true
	}
	if !walk(value) {
		return false
	}
	// encoding/json replaces invalid UTF-16 with U+FFFD; explicitly reject the invalid escape.
	for i := 0; i < len(raw); i++ {
		if raw[i] != '\\' {
			continue
		}
		i++
		if i >= len(raw) {
			return false
		}
		if raw[i] != 'u' {
			continue
		}
		if i+4 >= len(raw) {
			return false
		}
		n, err := strconv.ParseUint(string(raw[i+1:i+5]), 16, 16)
		if err != nil {
			return false
		}
		i += 4
		if n >= 0xd800 && n <= 0xdbff {
			if i+6 >= len(raw) || string(raw[i+1:i+3]) != "\\u" {
				return false
			}
			low, err := strconv.ParseUint(string(raw[i+3:i+7]), 16, 16)
			if err != nil || low < 0xdc00 || low > 0xdfff {
				return false
			}
			i += 6
		} else if n >= 0xdc00 && n <= 0xdfff {
			return false
		}
	}
	return true
}
