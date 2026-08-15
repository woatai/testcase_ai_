"""初始迁移：创建项目、知识、生成和审批相关的全部平台表。"""

from alembic import op

from testcase_ai.database import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """按照当前 Base.metadata 创建初始表结构。"""

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    """回滚初始版本并删除全部平台表；执行会丢失已有数据。"""

    Base.metadata.drop_all(bind=op.get_bind())
