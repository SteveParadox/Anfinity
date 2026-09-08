from __future__ import annotations

from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from app.storage import s3


S3_ENV_KEYS = [
    "ENVIRONMENT",
    "APP_ENV",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "S3_ENDPOINT_URL",
    "S3_BUCKET_NAME",
    "S3_REGION",
    "S3_CREATE_BUCKET",
]


@pytest.fixture(autouse=True)
def clean_s3_env(monkeypatch):
    for key in S3_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(
        s3,
        "settings",
        SimpleNamespace(
            AWS_ACCESS_KEY_ID="",
            AWS_SECRET_ACCESS_KEY="",
            S3_ENDPOINT_URL="http://localhost:9000",
            S3_BUCKET_NAME="anfinity",
            S3_REGION="us-east-1",
            S3_CREATE_BUCKET=None,
        ),
    )


def test_load_s3_config_uses_development_env_file_by_default(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "ENVIRONMENT=development",
                "AWS_ACCESS_KEY_ID=minioadmin",
                "AWS_SECRET_ACCESS_KEY=minioadmin",
                "S3_ENDPOINT_URL=http://localhost:9000",
                "S3_BUCKET_NAME=dev-bucket",
                "S3_REGION=us-east-1",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(s3, "SERVER_ROOT", tmp_path)

    config = s3._load_s3_config()

    assert config.environment == "development"
    assert config.bucket_name == "dev-bucket"
    assert config.endpoint_url == "http://localhost:9000"
    assert config.create_bucket is True


def test_load_s3_config_uses_production_env_file_when_requested(tmp_path, monkeypatch):
    (tmp_path / ".env.production").write_text(
        "\n".join(
            [
                "ENVIRONMENT=production",
                "AWS_ACCESS_KEY_ID=prod-access-key",
                "AWS_SECRET_ACCESS_KEY=prod-secret-key",
                "S3_ENDPOINT_URL=",
                "S3_BUCKET_NAME=prod-bucket",
                "S3_REGION=eu-north-1",
                "S3_CREATE_BUCKET=false",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(s3, "SERVER_ROOT", tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "production")

    config = s3._load_s3_config()

    assert config.environment == "production"
    assert config.bucket_name == "prod-bucket"
    assert config.endpoint_url is None
    assert config.region == "eu-north-1"
    assert config.create_bucket is False


def test_process_environment_overrides_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "ENVIRONMENT=development",
                "AWS_ACCESS_KEY_ID=minioadmin",
                "AWS_SECRET_ACCESS_KEY=minioadmin",
                "S3_BUCKET_NAME=file-bucket",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(s3, "SERVER_ROOT", tmp_path)
    monkeypatch.setenv("S3_BUCKET_NAME", "override-bucket")

    config = s3._load_s3_config()

    assert config.bucket_name == "override-bucket"


def test_s3_client_creates_aws_bucket_with_region(monkeypatch):
    fake_client = FakeS3Client(head_code="404")
    client_kwargs = {}

    def fake_boto_client(service_name, **kwargs):
        client_kwargs.update(kwargs)
        assert service_name == "s3"
        return fake_client

    monkeypatch.setattr(s3.boto3, "client", fake_boto_client)
    monkeypatch.setattr(
        s3,
        "_load_s3_config",
        lambda: s3.S3Config(
            environment="development",
            bucket_name="new-bucket",
            region="eu-north-1",
            endpoint_url=None,
            access_key_id="access-key",
            secret_access_key="secret-key",
            create_bucket=True,
        ),
    )

    s3.S3Client()

    assert client_kwargs["region_name"] == "eu-north-1"
    assert fake_client.created_bucket == {
        "Bucket": "new-bucket",
        "CreateBucketConfiguration": {"LocationConstraint": "eu-north-1"},
    }
    assert fake_client.waited_for_bucket == "new-bucket"


def test_s3_client_does_not_auto_create_bucket_when_disabled(monkeypatch):
    fake_client = FakeS3Client(head_code="404")
    monkeypatch.setattr(s3.boto3, "client", lambda *_args, **_kwargs: fake_client)
    monkeypatch.setattr(
        s3,
        "_load_s3_config",
        lambda: s3.S3Config(
            environment="production",
            bucket_name="missing-bucket",
            region="us-east-1",
            endpoint_url=None,
            access_key_id="access-key",
            secret_access_key="secret-key",
            create_bucket=False,
        ),
    )

    with pytest.raises(RuntimeError, match="does not exist or is not accessible"):
        s3.S3Client()

    assert fake_client.created_bucket is None


class FakeS3Client:
    def __init__(self, head_code: str | None = None):
        self.head_code = head_code
        self.created_bucket = None
        self.waited_for_bucket = None

    def head_bucket(self, Bucket):
        if self.head_code:
            raise ClientError(
                {"Error": {"Code": self.head_code, "Message": "head failed"}},
                "HeadBucket",
            )

    def create_bucket(self, **kwargs):
        self.created_bucket = kwargs

    def get_waiter(self, _name):
        return self

    def wait(self, Bucket):
        self.waited_for_bucket = Bucket
