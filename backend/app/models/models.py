from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, JSON, BigInteger, UniqueConstraint, ARRAY
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .base import Base
import uuid
from sqlalchemy.dialects.postgresql import UUID

def generate_uuid():
    return str(uuid.uuid4())

class Worker(Base):
    __tablename__ = "workers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False)
    base_url = Column(Text, nullable=False)
    api_key = Column(Text, nullable=True)
    enabled = Column(Boolean, default=True)
    status = Column(Text, nullable=False) # HEALTHY, UNHEALTHY
    priority = Column(Integer, default=0)
    max_concurrent_jobs = Column(Integer, default=1)
    current_queue_len = Column(Integer, default=0)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    tags = Column(JSON, nullable=True)

class Workflow(Base):
    __tablename__ = "workflows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False)
    version = Column(Text, nullable=False)
    max_batch_size = Column(Integer, nullable=False)
    prompt_nodes = Column(JSON, nullable=True) # List of node IDs
    seed_nodes = Column(JSON, nullable=True) # List of node IDs
    forbidden_nodes = Column(JSON, nullable=True)
    raw_definition = Column(JSON, nullable=False)
    validated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class VariablePool(Base):
    __tablename__ = "variable_pools"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False)
    mode = Column(Text, nullable=False) # no_replacement, permutation
    items = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Task(Base):
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id"), nullable=False)
    state = Column(Text, nullable=False) # DRAFT, FROZEN, RUNNING, DONE, FAILED
    notes = Column(Text, nullable=True)
    frozen_snapshot = Column(JSON, nullable=True) # prompts, seeds, workflow version
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    workflow = relationship("Workflow")
    prompts = relationship("Prompt", back_populates="task")

class Prompt(Base):
    __tablename__ = "prompts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    text = Column(Text, nullable=False)
    variables = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    task = relationship("Task", back_populates="prompts")
    seeds = relationship("Seed", back_populates="prompt")

class Seed(Base):
    __tablename__ = "seeds"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prompt_id = Column(UUID(as_uuid=True), ForeignKey("prompts.id"), nullable=False)
    seed_value = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    prompt = relationship("Prompt", back_populates="seeds")
    generations = relationship("Generation", back_populates="seed")

class Generation(Base):
    __tablename__ = "generations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prompt_id = Column(UUID(as_uuid=True), ForeignKey("prompts.id"), nullable=False)
    seed_id = Column(UUID(as_uuid=True), ForeignKey("seeds.id"), nullable=False)
    worker_id = Column(UUID(as_uuid=True), ForeignKey("workers.id"), nullable=True)
    state = Column(Text, nullable=False) # pending, running, done, failed
    image_uri = Column(Text, nullable=True)
    checksum = Column(Text, nullable=True)
    retries_used = Column(Integer, default=0)
    metadata_json = Column(JSON, nullable=True) # renamed from metadata to avoid conflict
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    seed = relationship("Seed", back_populates="generations")

class Annotation(Base):
    __tablename__ = "annotations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id = Column(UUID(as_uuid=True), nullable=False)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    prompt_id = Column(UUID(as_uuid=True), ForeignKey("prompts.id"), nullable=False)
    chosen_index = Column(Integer, nullable=False)
    rejected_index = Column(Integer, nullable=True)
    spam = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user_id = Column(UUID(as_uuid=True), nullable=True)
    variant_key = Column(Text, nullable=True)

class ABTest(Base):
    __tablename__ = "ab_tests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, unique=True, nullable=False)
    variants = Column(JSON, nullable=False) # [{key:"A", weight:0.5}, ...]
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class ABAssignment(Base):
    __tablename__ = "ab_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_id = Column(UUID(as_uuid=True), ForeignKey("ab_tests.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    variant_key = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint('test_id', 'user_id', name='_test_user_uc'),)
