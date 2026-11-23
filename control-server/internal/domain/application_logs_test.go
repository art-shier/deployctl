package domain

import (
	"encoding/json"
	"testing"
	"time"
)

func TestApplicationLogPolicy(t *testing.T) {
	a := []LogAttribute{{Type: "device", Value: "install-1"}, {Type: "user", Value: "user-1"}}
	p := LogPolicy{Version: 1, Rules: []LogRule{{ID: "u", Name: "user", Kind: "specified", Attribute: "user", Values: []string{"user-1"}, Enabled: true}}}
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}
	if d := p.Decide("p", "prod", a); !d.Allowed || len(d.MatchedRules) != 1 {
		t.Fatal(d)
	}
	p.Rules[0].Enabled = false
	if p.Decide("p", "prod", a).Allowed {
		t.Fatal("disabled matched")
	}
	p.Rules = []LogRule{{ID: "g", Name: "gray", Kind: "percentage", Percentage: 100, Enabled: true}}
	if !p.Decide("p", "prod", a).Allowed || p.Decide("p", "prod", nil).Allowed {
		t.Fatal("100% needs device")
	}
	for i := 0; i < 100; i++ {
		p.Rules[0].Percentage = float64(i)
		x := p.Decide("p", "prod", a).Allowed
		if p.Decide("p", "prod", a).Allowed != x {
			t.Fatal("unstable")
		}
		p.Rules[0].Percentage = float64(i + 1)
		if x && !p.Decide("p", "prod", a).Allowed {
			t.Fatal("not monotonic")
		}
	}
	p.Rules[0].Percentage = 0
	if p.Decide("p", "prod", a).Allowed {
		t.Fatal("zero matched")
	}
	p.Rules[0].Percentage = 101
	if p.Validate() == nil {
		t.Fatal("invalid percentage")
	}
	if ValidateLogAttributes([]LogAttribute{{Type: "user", Value: "a"}, {Type: "user", Value: "b"}}) == nil {
		t.Fatal("duplicate attributes")
	}
}
func TestApplicationLogEventValidation(t *testing.T) {
	e := ApplicationLog{SchemaVersion: 1, EventID: "e", OccurredAt: time.Now(), Level: "INFO", Source: "android", Event: "record.saved", Message: "saved", Data: json.RawMessage(`{"duration":343}`)}
	if e.Validate() != nil {
		t.Fatal("valid event")
	}
	e.Data = json.RawMessage(`[]`)
	if e.Validate() == nil {
		t.Fatal("array data")
	}
	e.Data = nil
	e.SchemaVersion = 2
	if e.Validate() == nil {
		t.Fatal("unknown schema")
	}
}

func TestApplicationLogRejectsPostgresIncompatibleJSONAndTime(t *testing.T) {
	e := ApplicationLog{SchemaVersion: 1, EventID: "e", OccurredAt: time.Now(), Level: "INFO", Source: "web", Event: "saved", Message: "ok"}
	for _, data := range []string{`{"nested":{"x":"\u0000"}}`, `{"\u0000":"value"}`, `{"x":"\ud800"}`, `{"n":1e999999}`} {
		e.Data = json.RawMessage(data)
		if e.Validate() == nil {
			t.Fatalf("accepted incompatible JSON: %s", data)
		}
	}
	e.Data = nil
	e.OccurredAt = time.Date(0, 1, 1, 0, 0, 0, 0, time.UTC)
	if e.Validate() == nil {
		t.Fatal("accepted year zero")
	}
}
