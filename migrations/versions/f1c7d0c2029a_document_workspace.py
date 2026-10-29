"""Versioned document workspace and approval-bound changesets."""

from alembic import op

from shopresolve.documents.models import ChangeSet, Revision, Workspace

revision = "f1c7d0c2029a"
down_revision = "d3cf3cd9b079"
branch_labels = None
depends_on = None


def upgrade():
    for table in (Workspace.__table__, Revision.__table__, ChangeSet.__table__):
        table.create(op.get_bind())


def downgrade():
    for table in (ChangeSet.__table__, Revision.__table__, Workspace.__table__):
        table.drop(op.get_bind())
