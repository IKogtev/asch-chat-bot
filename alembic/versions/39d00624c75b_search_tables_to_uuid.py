"""search tables to uuid

Revision ID: 39d00624c75b
Revises: 595c413fc9cc
Create Date: 2026-05-04 16:25:22.878398

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '39d00624c75b'
down_revision = '595c413fc9cc'
branch_labels = None
depends_on = None


def upgrade() -> None:
     # если таблицы уже есть — удаляем
    op.execute("DROP TABLE IF EXISTS search_results")
    op.execute("DROP TABLE IF EXISTS search_meta")

    # создаём заново с UUID
    op.create_table(
        'search_meta',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('session_id', sa.Text(), nullable=False),
        sa.Column('search_id', sa.Text(), nullable=False),
        sa.Column('query', sa.Text(), nullable=False),
        sa.Column('total_count', sa.Integer(), nullable=False),
        sa.Column('shown_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('user_id', 'session_id')
    )

    op.create_table(
        'search_results',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('session_id', sa.Text(), nullable=False),
        sa.Column('search_id', sa.Text(), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Text(), nullable=False),
        sa.Column('source_name', sa.Text(), nullable=False),
        sa.Column('source_path', sa.Text(), nullable=True),
        sa.Column('score', sa.DOUBLE_PRECISION(), nullable=True),
        sa.Column('snippet', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'idx_search_results_user_session', 
        'search_results', 
        ['user_id', 'session_id', 'rank']
    )
    op.create_index(
        'idx_search_results_search_id', 
        'search_results', 
        ['search_id']
    )


def downgrade() -> None:
    op.drop_index('idx_search_results_search_id', table_name='search_results')
    op.drop_index('idx_search_results_user_session', table_name='search_results')
    op.drop_table('search_results')
    op.drop_table('search_meta')
