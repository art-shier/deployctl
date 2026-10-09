package store

import (
	"context"
	"slices"

	"github.com/jackc/pgx/v5"
)

// Project binding changes share the image-operation locks. Lock repository names
// in order when changing a binding; never lock other projects after these locks.
func lockRepositories(ctx context.Context, tx pgx.Tx, repositories ...string) error {
	slices.Sort(repositories)
	for _, repository := range slices.Compact(repositories) {
		if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "ctl-images:"+repository); err != nil {
			return err
		}
	}
	return nil
}
