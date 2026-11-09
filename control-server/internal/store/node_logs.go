package store

import (
	"context"
	"github.com/art-shier/deployctl/control-server/internal/domain"
)

// AuthorizeLogQuery is called at enqueue, node delivery, and browser response.
// Read the actor, group membership, node grants and instance/task ownership anew.
func (s *Store) AuthorizeLogQuery(ctx context.Context, actor string, q domain.LogQuery) (domain.Instance, domain.Node, error) {
	var instance domain.Instance
	var node domain.Node
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return instance, node, err
	}
	defer tx.Rollback(ctx)
	instance, err = scanInstance(tx.QueryRow(ctx, "SELECT data FROM ctl_instances WHERE id=$1 AND project=$2", q.InstanceID, q.Project))
	if err != nil {
		return instance, node, err
	}
	if q.NodeID != "" && q.NodeID != instance.NodeID {
		return instance, node, domain.ErrForbidden
	}
	node, err = lockRemoteNode(ctx, tx, instance.NodeID, "SHARE")
	if err != nil {
		return instance, node, err
	}
	project, err := scanProject(tx.QueryRow(ctx, "SELECT data FROM ctl_projects WHERE slug=$1 AND deleted_at IS NULL FOR SHARE", instance.Project))
	if err != nil {
		return instance, node, err
	}
	p, err := remotePrincipal(ctx, tx, actor)
	if err != nil {
		return instance, node, err
	}
	if !p.CanInGroup("resolve", project.Slug, project.Group, instance.Environment) || !node.Allows(project, instance.Environment) {
		return instance, node, domain.ErrForbidden
	}
	if q.Source == "deployment" {
		var found bool
		err = tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ctl_remote_tasks WHERE id=$1 AND instance_id=$2 AND node_id=$3 AND project=$4)", q.TaskID, instance.ID, node.ID, project.Slug).Scan(&found)
		if err != nil {
			return instance, node, err
		}
		if !found {
			return instance, node, domain.ErrNotFound
		}
	}
	return instance, node, tx.Commit(ctx)
}
