package store

import (
	"context"
	"encoding/json"
	"errors"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/art-shier/deployctl/control-server/internal/domain"
	"github.com/jackc/pgx/v5"
)

func TestGroupRevisionEncryptionOptimisticWritesAndAudit(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	cfg := domain.Configuration{RuntimeEnv: map[string]domain.Variable{"PASSWORD": {Value: "private-group-sentinel", Secret: true}}, InstallParams: map[string]domain.Variable{"TOKEN": {Value: "private-install-sentinel", Secret: true}}}
	first, err := s.SaveGroupRevision(ctx, "default", "prod", 0, cfg, "owner")
	if err != nil || first.Revision != 1 {
		t.Fatal(first, err)
	}
	if _, err = s.SaveGroupRevision(ctx, "default", "prod", 0, cfg, "owner"); !errors.Is(err, domain.ErrConflict) {
		t.Fatal("stale revision accepted", err)
	}
	read, err := s.GetGroupRevision(ctx, "default", "prod")
	if err != nil || read.Configuration.RuntimeEnv["PASSWORD"].Value != "private-group-sentinel" || read.Configuration.InstallParams["TOKEN"].Value != "private-install-sentinel" {
		t.Fatal("group secret lost", err)
	}
	var encrypted []byte
	if err = s.pool.QueryRow(ctx, "SELECT ciphertext FROM ctl_group_revisions WHERE id=$1", first.ID).Scan(&encrypted); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encrypted), "private-group-sentinel") || strings.Contains(string(encrypted), "private-install-sentinel") {
		t.Fatal("plaintext group ciphertext")
	}
	for _, aad := range []string{"default/prod/" + first.ID, "group/default/stage/" + first.ID, "group/other/prod/" + first.ID} {
		if _, err = s.cipher.Open(encrypted, aad); err == nil {
			t.Fatal("ciphertext accepted outside group scope", aad)
		}
	}
	var auditJSON string
	if err = s.pool.QueryRow(ctx, "SELECT COALESCE(jsonb_agg(data)::text,'[]') FROM ctl_audit").Scan(&auditJSON); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(auditJSON, "private-group-sentinel") || strings.Contains(auditJSON, "private-install-sentinel") || !strings.Contains(auditJSON, "group.configuration.save") {
		t.Fatal("unsafe or absent audit", auditJSON)
	}
	var wg sync.WaitGroup
	results := make(chan error, 2)
	for _, value := range []string{"first-writer", "second-writer"} {
		wg.Add(1)
		go func(value string) {
			defer wg.Done()
			_, err := s.SaveGroupRevision(ctx, "default", "prod", 1, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"PASSWORD": {Value: value, Secret: true}}}, "owner")
			results <- err
		}(value)
	}
	wg.Wait()
	close(results)
	won, conflicted := 0, 0
	for err := range results {
		if err == nil {
			won++
		} else if errors.Is(err, domain.ErrConflict) {
			conflicted++
		} else {
			t.Fatal(err)
		}
	}
	if won != 1 || conflicted != 1 {
		t.Fatal("optimistic concurrent writes", won, conflicted)
	}
	var count int
	if err = s.pool.QueryRow(ctx, "SELECT count(*) FROM ctl_group_revisions WHERE group_slug='default' AND environment='prod'").Scan(&count); err != nil || count != 2 {
		t.Fatal("unexpected immutable revisions", count, err)
	}
	old, err := s.readGroupRevision(s.pool.QueryRow(ctx, "SELECT id,group_slug,environment,revision,ciphertext,created_at FROM ctl_group_revisions WHERE id=$1", first.ID))
	if err != nil || old.Configuration.RuntimeEnv["PASSWORD"].Value != "private-group-sentinel" {
		t.Fatal("immutable group revision changed", err)
	}
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	current, err := s.GetGroupRevision(ctx, "default", "prod")
	if err != nil || current.Revision != 2 {
		t.Fatal("repeat migration lost group configuration", current, err)
	}
}

func TestGroupRevisionValidationAndFailedCreationLeavesNoEnvironment(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	for _, tc := range []struct {
		group, env string
		expected   int64
		cfg        domain.Configuration
		want       error
	}{
		{"missing", "prod", 0, domain.Configuration{}, domain.ErrNotFound},
		{"default", "invalid_env", 0, domain.Configuration{}, domain.ErrInvalid},
		{"default", "new", -1, domain.Configuration{}, domain.ErrInvalid},
		{"default", "new", 1, domain.Configuration{}, domain.ErrConflict},
		{"default", "new", 0, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"APP_VERSION": {Value: "bad"}}}, domain.ErrInvalid},
		{"default", "new", 0, domain.Configuration{DeploymentDefaults: domain.DeploymentDefaults{HostPort: 8080}}, domain.ErrInvalid},
	} {
		if _, err := s.SaveGroupRevision(ctx, tc.group, tc.env, tc.expected, tc.cfg, "owner"); !errors.Is(err, tc.want) {
			t.Fatalf("%+v: %v", tc, err)
		}
	}
	names, err := s.ListGroupEnvironments(ctx, "default")
	if err != nil || len(names) != 0 {
		t.Fatal("failed creation left environment", names, err)
	}
}

func TestGroupInheritanceFollowsMembershipAndEnvironment(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	_, err := s.CreateGroup(ctx, domain.Group{Slug: "apps", Name: "Apps"}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.SaveGroupRevision(ctx, "default", "prod", 0, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"SOURCE": {Value: "default"}}, InstallParams: map[string]domain.Variable{"ROOT": {Value: "/default"}}}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.SaveGroupRevision(ctx, "apps", "prod", 0, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"SOURCE": {Value: "apps"}, "EMPTY": {Value: "group"}}}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.SaveGroupRevision(ctx, "apps", "stage", 0, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"SOURCE": {Value: "stage"}}}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.SaveRevision(ctx, "notes", "prod", 1, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"EMPTY": {Value: ""}}, DeploymentDefaults: domain.DeploymentDefaults{HostPort: 8090}}, "stable", "owner")
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.PublishRelease(ctx, domain.Release{Project: "notes", Version: "v1.0.0", Image: "registry.example/notes@sha256:" + strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), Size: 10}, true, "owner")
	if err != nil {
		t.Fatal(err)
	}
	before, err := s.Resolve(ctx, "notes", "prod", "")
	if err != nil || before.Revision.Configuration.RuntimeEnv["SOURCE"].Value != "default" {
		t.Fatal(before, err)
	}
	if _, err = s.MoveProjectGroup(ctx, "notes", "default", "apps", "owner"); err != nil {
		t.Fatal(err)
	}
	after, err := s.Resolve(ctx, "notes", "prod", "")
	if err != nil || after.Revision.ID == before.Revision.ID || after.Revision.Configuration.RuntimeEnv["SOURCE"].Value != "apps" || after.Revision.Configuration.RuntimeEnv["EMPTY"].Value != "" || len(after.Revision.Configuration.InstallParams) != 0 || after.Revision.Configuration.DeploymentDefaults.HostPort != 8090 {
		t.Fatal("membership inheritance", after, err)
	}
	names, err := s.ListEnvironments(ctx, "notes")
	if err != nil || !slices.Equal(names, []string{"prod", "stage"}) {
		t.Fatal(names, err)
	}
	stage, err := s.GetRevision(ctx, "notes", "stage")
	if err != nil || stage.Revision != 0 || len(stage.Configuration.RuntimeEnv) != 0 || stage.GroupSource == nil || stage.GroupSource.Slug != "apps" || stage.InheritedConfiguration.RuntimeEnv["SOURCE"].Value != "stage" {
		t.Fatal(stage, err)
	}
	if _, err = s.GetRevision(ctx, "notes", "qa"); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("missing env inherited", err)
	}
	if _, err = s.MoveProjectGroup(ctx, "notes", "apps", "default", "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.Resolve(ctx, "notes", "stage", ""); !errors.Is(err, domain.ErrNotFound) {
		t.Fatal("old group env remained inherited", err)
	}
	restored, err := s.Resolve(ctx, "notes", "prod", "")
	if err != nil || restored.Revision.ID != before.Revision.ID {
		t.Fatal("same source inputs changed effective ID", restored, err)
	}
}

func TestProjectRevisionSnapshotKeepsMembershipAndGroupRevisionTogether(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	first, err := s.SaveGroupRevision(ctx, "default", "prod", 0, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"SOURCE": {Value: "before"}}}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.CreateGroup(ctx, domain.Group{Slug: "apps", Name: "Apps"}, "owner"); err != nil {
		t.Fatal(err)
	}
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	p, err := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug='notes'"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.MoveProjectGroup(ctx, "notes", "default", "apps", "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.SaveGroupRevision(ctx, "default", "prod", 1, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"SOURCE": {Value: "after"}}}, "owner"); err != nil {
		t.Fatal(err)
	}
	r, err := s.projectRevision(ctx, tx, p, "prod")
	if err != nil || r.GroupSource == nil || r.GroupSource.ID != first.ID || r.InheritedConfiguration.RuntimeEnv["SOURCE"].Value != "before" {
		t.Fatal("mixed configuration snapshot", r, err)
	}
	current, err := s.GetRevision(ctx, "notes", "prod")
	if err != nil || current.GroupSource != nil || len(current.InheritedConfiguration.RuntimeEnv) != 0 {
		t.Fatal("membership did not update", current, err)
	}
}

func TestEffectiveConfigurationIdentityAndMerge(t *testing.T) {
	p := domain.Project{Slug: "notes", Group: "apps"}
	r := domain.Revision{ID: "project-revision", Environment: "prod", Revision: 2, GroupSource: &domain.GroupSource{Slug: "apps", ID: "group-revision", Revision: 3}, Configuration: domain.Configuration{RuntimeEnv: map[string]domain.Variable{"SHARED": {Value: "", Secret: true}}, DeploymentDefaults: domain.DeploymentDefaults{HostPort: 8080}}, InheritedConfiguration: domain.Configuration{RuntimeEnv: map[string]domain.Variable{"SHARED": {Value: "group"}, "GROUP_ONLY": {Value: "yes"}}, InstallParams: map[string]domain.Variable{"ROOT": {Value: "/group"}}}}
	got := effectiveRevision(p, r)
	if !regexp.MustCompile(`^[a-f0-9]{32}$`).MatchString(got.ID) || got.Revision != 3 || got.Configuration.RuntimeEnv["SHARED"] != (domain.Variable{Value: "", Secret: true}) || got.Configuration.RuntimeEnv["GROUP_ONLY"].Value != "yes" || got.Configuration.InstallParams["ROOT"].Value != "/group" || got.Configuration.DeploymentDefaults.HostPort != 8080 {
		t.Fatal(got)
	}
	if len(r.Configuration.RuntimeEnv) != 1 || r.InheritedConfiguration.RuntimeEnv["SHARED"].Value != "group" {
		t.Fatal("source maps mutated")
	}
	if effectiveRevision(p, r).ID != got.ID {
		t.Fatal("unstable effective ID")
	}
	for _, field := range []string{"project", "group", "env", "own-revision", "group-revision"} {
		otherP, otherR := p, r
		source := *r.GroupSource
		otherR.GroupSource = &source
		switch field {
		case "project":
			otherP.Slug = "other"
		case "group":
			otherP.Group = "other"
		case "env":
			otherR.Environment = "stage"
		case "own-revision":
			otherR.ID = "other"
		case "group-revision":
			otherR.GroupSource.ID = "other"
		}
		if effectiveRevision(otherP, otherR).ID == got.ID {
			t.Fatal("identity omitted", field)
		}
	}
	encoded, _ := json.Marshal(r)
	if strings.Contains(string(encoded), "GROUP_ONLY") || strings.Contains(string(encoded), "group-revision") {
		t.Fatal("domain serialization exposed inherited maps")
	}
}

func TestResolveRejectsMergedConfigurationBeyondClientLimits(t *testing.T) {
	s := fixture(t)
	ctx := context.Background()
	groupVars, projectVars := map[string]domain.Variable{}, map[string]domain.Variable{}
	for i := range 128 {
		groupVars["GROUP_"+strconv.Itoa(i)] = domain.Variable{Value: "group"}
		projectVars["PROJECT_"+strconv.Itoa(i)] = domain.Variable{Value: "project"}
	}
	if _, err := s.SaveGroupRevision(ctx, "default", "prod", 0, domain.Configuration{RuntimeEnv: groupVars}, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.SaveRevision(ctx, "notes", "prod", 1, domain.Configuration{RuntimeEnv: projectVars}, "stable", "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.PublishRelease(ctx, domain.Release{Project: "notes", Version: "v1.0.0", Image: "registry.example/notes@sha256:" + strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), Size: 10}, true, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Resolve(ctx, "notes", "prod", ""); !errors.Is(err, domain.ErrInvalid) {
		t.Fatal("oversized effective configuration returned to CLI", err)
	}
}

func TestResolveSnapshotCannotMixMembershipAndGroupChanges(t *testing.T) {
	s := fixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	first, err := s.SaveGroupRevision(ctx, "default", "prod", 0, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"SOURCE": {Value: "before"}}}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	second, err := s.SaveGroupRevision(ctx, "default", "prod", 1, domain.Configuration{RuntimeEnv: map[string]domain.Variable{"SOURCE": {Value: "after"}}}, "owner")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.CreateGroup(ctx, domain.Group{Slug: "apps", Name: "Apps"}, "owner"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.pool.Exec(ctx, "UPDATE ctl_group_environments SET current_id=$1,revision=1 WHERE group_slug='default' AND name='prod'", first.ID); err != nil {
		t.Fatal(err)
	}
	if _, err = s.PublishRelease(ctx, domain.Release{Project: "notes", Version: "v1.0.0", Image: "registry.example/notes@sha256:" + strings.Repeat("a", 64), SHA256: strings.Repeat("b", 64), Size: 10}, true, "owner"); err != nil {
		t.Fatal(err)
	}
	writer, err := s.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Rollback(context.Background())
	// Hold the group table until the real Resolve has read project membership.
	if _, err = writer.Exec(ctx, "LOCK TABLE ctl_group_environments IN ACCESS EXCLUSIVE MODE"); err != nil {
		t.Fatal(err)
	}
	type result struct {
		resolved Resolved
		err      error
	}
	finished := make(chan result, 1)
	go func() { resolved, err := s.Resolve(ctx, "notes", "prod", ""); finished <- result{resolved, err} }()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		var waiting bool
		if err = s.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE '%FROM ctl_group_environments e JOIN ctl_group_revisions%')`).Scan(&waiting); err != nil {
			t.Fatal(err)
		}
		if waiting {
			break
		}
		select {
		case outcome := <-finished:
			t.Fatal("Resolve did not wait for group snapshot", outcome.err)
		case <-ctx.Done():
			t.Fatal("Resolve did not reach group read", ctx.Err())
		case <-ticker.C:
		}
	}
	if _, err = writer.Exec(ctx, `UPDATE ctl_projects SET group_slug='apps',data=jsonb_set(data,'{group}','"apps"') WHERE slug='notes'`); err != nil {
		t.Fatal(err)
	}
	if _, err = writer.Exec(ctx, "UPDATE ctl_group_environments SET current_id=$1,revision=2 WHERE group_slug='default' AND name='prod'", second.ID); err != nil {
		t.Fatal(err)
	}
	if err = writer.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	outcome := <-finished
	if outcome.err != nil || outcome.resolved.Project.Group != "default" || outcome.resolved.Revision.GroupSource == nil || outcome.resolved.Revision.GroupSource.ID != first.ID || outcome.resolved.Revision.Configuration.RuntimeEnv["SOURCE"].Value != "before" {
		t.Fatal("Resolve mixed membership and configuration snapshots", outcome.resolved, outcome.err)
	}
}
