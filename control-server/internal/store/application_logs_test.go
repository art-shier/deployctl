package store_test

import (
	"context"
	"errors"
	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/art-shier/deployctl/control-server/internal/store"
	"github.com/art-shier/deployctl/control-server/internal/testutil"
	"testing"
	"time"
)

func TestApplicationLogsAdmissionIsolationAndDedupe(t *testing.T) {
	s := testutil.Store(t)
	ctx := context.Background()
	s.CreateProject(ctx, domain.Project{Slug: "notes", Name: "Notes", ImageRepository: "registry.test/notes"}, "owner")
	p := domain.LogPolicy{Rules: []domain.LogRule{{ID: "all", Name: "all", Kind: "percentage", Percentage: 100, Enabled: true}}}
	p, err := s.SaveLogPolicy(ctx, "notes", "prod", "owner", p)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.SaveLogPolicy(ctx, "notes", "prod", "owner", domain.LogPolicy{}); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("stale version", err)
	}
	e := domain.ApplicationLog{SchemaVersion: 1, EventID: "e1", OccurredAt: time.Now(), Level: "INFO", Source: "android", Event: "saved", Message: "ok", DeviceID: "d"}
	a, err := s.AppendApplicationLogs(ctx, "notes", "prod", "owner", []domain.ApplicationLog{e})
	if err != nil || a.Accepted != 1 {
		t.Fatal(a, err)
	}
	a, err = s.AppendApplicationLogs(ctx, "notes", "prod", "owner", []domain.ApplicationLog{e})
	if err != nil || a.Duplicates != 1 {
		t.Fatal(a, err)
	}
	s.SaveRevision(ctx, "notes", "other", 0, domain.Configuration{}, "stable", "owner")
	a, err = s.AppendApplicationLogs(ctx, "notes", "other", "owner", []domain.ApplicationLog{e})
	if err != nil || a.Rejected != 1 {
		t.Fatal(a, err)
	}
	page, err := s.QueryApplicationLogs(ctx, "notes", "prod", "owner", store.ApplicationLogQuery{Limit: 1})
	if err != nil || len(page.Items) != 1 {
		t.Fatal(page, err)
	}
	p.Rules = nil
	_, err = s.SaveLogPolicy(ctx, "notes", "prod", "owner", p)
	if err != nil {
		t.Fatal(err)
	}
	e.EventID = "e2"
	a, err = s.AppendApplicationLogs(ctx, "notes", "prod", "owner", []domain.ApplicationLog{e})
	if err != nil || a.Rejected != 1 {
		t.Fatal("revocation", a, err)
	}
}
