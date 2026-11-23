package store

import (
	"context"
	"errors"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"testing"
	"time"
)

func TestApplicationLogEvictionRetainsDedupeAndRejectsUnknownEnvironment(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	_, err := s.SaveLogPolicy(ctx, "notes", "typo", "owner", domain.LogPolicy{})
	if !errors.Is(err, domain.ErrNotFound) {
		t.Fatalf("unknown environment: %v", err)
	}
	_, err = s.SaveLogPolicy(ctx, "notes", "prod", "owner", domain.LogPolicy{Rules: []domain.LogRule{{ID: "all", Name: "all", Kind: "percentage", Enabled: true, Percentage: 100}}})
	if err != nil {
		t.Fatal(err)
	}
	e := domain.ApplicationLog{SchemaVersion: 1, EventID: "same", OccurredAt: time.Now(), Level: "INFO", Source: "web", Event: "saved", Message: "ok", DeviceID: "d"}
	_, err = s.AppendApplicationLogs(ctx, "notes", "prod", "owner", []domain.ApplicationLog{e})
	if err != nil {
		t.Fatal(err)
	}
	// Simulate content eviction independently of dedup retention.
	_, err = s.pool.Exec(ctx, "DELETE FROM ctl_application_logs WHERE event_id='same'")
	if err != nil {
		t.Fatal(err)
	}
	result, err := s.AppendApplicationLogs(ctx, "notes", "prod", "owner", []domain.ApplicationLog{e})
	if err != nil || result.Duplicates != 1 {
		t.Fatal("late retry duplicated", result, err)
	}
}
func TestApplicationLogCursorAndExpiredRows(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	s.SaveLogPolicy(ctx, "notes", "prod", "owner", domain.LogPolicy{Rules: []domain.LogRule{{ID: "g", Name: "g", Kind: "percentage", Enabled: true, Percentage: 100}}})
	for _, id := range []string{"a", "b", "c"} {
		_, err := s.AppendApplicationLogs(ctx, "notes", "prod", "owner", []domain.ApplicationLog{{SchemaVersion: 1, EventID: id, OccurredAt: time.Now(), Level: "INFO", Source: "web", Event: "saved", Message: "ok", DeviceID: "d"}})
		if err != nil {
			t.Fatal(err)
		}
	}
	page, err := s.QueryApplicationLogs(ctx, "notes", "prod", "owner", ApplicationLogQuery{Limit: 2})
	if err != nil || len(page.Items) != 2 || page.NextCursor == 0 {
		t.Fatal(page, err)
	}
	next, err := s.QueryApplicationLogs(ctx, "notes", "prod", "owner", ApplicationLogQuery{Limit: 2, Before: page.NextCursor})
	if err != nil || len(next.Items) != 1 || next.Items[0].EventID != "a" {
		t.Fatal(next, err)
	}
	s.pool.Exec(ctx, "UPDATE ctl_application_logs SET received_at=now()-interval '4 days'")
	page, err = s.QueryApplicationLogs(ctx, "notes", "prod", "owner", ApplicationLogQuery{})
	if err != nil || len(page.Items) != 0 {
		t.Fatal(page, err)
	}
	if err = s.PruneApplicationLogs(ctx); err != nil {
		t.Fatal(err)
	}
}
