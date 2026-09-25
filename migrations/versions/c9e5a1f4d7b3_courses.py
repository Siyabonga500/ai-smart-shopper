"""short courses: courses, questions, answers and attempts

Revision ID: c9e5a1f4d7b3
Revises: b8d4f0e3c6a2
Create Date: 2026-09-26 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c9e5a1f4d7b3'
down_revision = 'b8d4f0e3c6a2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'courses',
        sa.Column('CourseId', sa.String(length=30), nullable=False),
        sa.Column('Slug', sa.String(length=80), nullable=False),
        sa.Column('Title', sa.String(length=120), nullable=False),
        sa.Column('Summary', sa.String(length=300), nullable=False),
        sa.Column('Content', sa.Text(), nullable=False),
        sa.Column('Minutes', sa.Integer(), nullable=False),
        sa.Column('Icon', sa.String(length=40), nullable=False),
        sa.Column('Position', sa.Integer(), nullable=False),
        sa.Column('IsPublished', sa.Boolean(), server_default=sa.text('1'), nullable=False),
        sa.Column('CreatedOn', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('CourseId'),
        sa.UniqueConstraint('Slug'),
    )
    op.create_table(
        'course_questions',
        sa.Column('QuestionId', sa.String(length=30), nullable=False),
        sa.Column('CourseId', sa.String(length=30), nullable=False),
        sa.Column('Text', sa.String(length=300), nullable=False),
        sa.Column('Explanation', sa.String(length=300), nullable=True),
        sa.Column('Position', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['CourseId'], ['courses.CourseId'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('QuestionId'),
    )
    with op.batch_alter_table('course_questions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_course_questions_CourseId'), ['CourseId'], unique=False)
    op.create_table(
        'course_answers',
        sa.Column('AnswerId', sa.String(length=30), nullable=False),
        sa.Column('QuestionId', sa.String(length=30), nullable=False),
        sa.Column('Text', sa.String(length=200), nullable=False),
        sa.Column('IsCorrect', sa.Boolean(), server_default=sa.text('0'), nullable=False),
        sa.Column('Position', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['QuestionId'], ['course_questions.QuestionId'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('AnswerId'),
    )
    with op.batch_alter_table('course_answers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_course_answers_QuestionId'), ['QuestionId'], unique=False)
    op.create_table(
        'course_attempts',
        sa.Column('AttemptId', sa.String(length=30), nullable=False),
        sa.Column('CourseId', sa.String(length=30), nullable=False),
        sa.Column('UserId', sa.String(length=30), nullable=False),
        sa.Column('Score', sa.Integer(), nullable=False),
        sa.Column('Total', sa.Integer(), nullable=False),
        sa.Column('Choices', sa.JSON(), nullable=True),
        sa.Column('CreatedOn', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['CourseId'], ['courses.CourseId'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['UserId'], ['users.UserId'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('AttemptId'),
    )
    with op.batch_alter_table('course_attempts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_course_attempts_CourseId'), ['CourseId'], unique=False)
        batch_op.create_index(batch_op.f('ix_course_attempts_UserId'), ['UserId'], unique=False)


def downgrade():
    with op.batch_alter_table('course_attempts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_course_attempts_UserId'))
        batch_op.drop_index(batch_op.f('ix_course_attempts_CourseId'))
    op.drop_table('course_attempts')
    with op.batch_alter_table('course_answers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_course_answers_QuestionId'))
    op.drop_table('course_answers')
    with op.batch_alter_table('course_questions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_course_questions_CourseId'))
    op.drop_table('course_questions')
    op.drop_table('courses')
