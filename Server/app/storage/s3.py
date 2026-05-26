"""S3-compatible storage client."""
import hashlib
import io
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import dotenv_values, load_dotenv


logger = logging.getLogger(__name__)

SERVER_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ENVS = {"prod", "production"}
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class S3Config:
    """Resolved S3 settings with environment-file selection already applied."""

    environment: str
    bucket_name: str
    region: str
    endpoint_url: Optional[str]
    access_key_id: Optional[str]
    secret_access_key: Optional[str]
    create_bucket: bool


def _normalize_env(value: Optional[str]) -> str:
    return (value or "development").strip().lower()


def _is_production(environment: Optional[str]) -> bool:
    return _normalize_env(environment) in PRODUCTION_ENVS


def _env_file_for(environment: str) -> Path:
    return SERVER_ROOT / (".env.production" if _is_production(environment) else ".env")


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {
        key: value
        for key, value in dotenv_values(path).items()
        if value is not None
    }


def _detect_environment() -> str:
    """Return the active backend environment before app settings are loaded."""
    explicit_environment = os.getenv("ENVIRONMENT") or os.getenv("APP_ENV")
    if explicit_environment:
        return _normalize_env(explicit_environment)

    development_values = _read_env_file(SERVER_ROOT / ".env")
    configured_environment = (
        development_values.get("ENVIRONMENT")
        or development_values.get("APP_ENV")
    )
    return _normalize_env(configured_environment)


def _load_selected_env_file() -> tuple[str, dict[str, str], Path]:
    """Load the selected env file without overriding real environment values."""
    environment = _detect_environment()
    env_path = _env_file_for(environment)
    file_values = _read_env_file(env_path)

    if env_path.exists():
        load_dotenv(env_path, override=False)
    else:
        logger.warning("S3 env file not found: %s", env_path)

    return environment, file_values, env_path


_BOOT_ENVIRONMENT, _BOOT_ENV_VALUES, _BOOT_ENV_FILE = _load_selected_env_file()

# Import after loading the selected env file so pydantic-settings sees the
# backend env when this module is the first app config import.
from app.config import settings  # noqa: E402


def _env_value(
    key: str,
    file_values: dict[str, str],
    default: Optional[str] = None,
) -> Optional[str]:
    value = os.getenv(key)
    if value is None:
        value = file_values.get(key)
    if value is None:
        value = getattr(settings, key, default)
    if value is None:
        return default
    return str(value)


def _optional_env_value(
    key: str,
    file_values: dict[str, str],
    default: Optional[str] = None,
) -> Optional[str]:
    value = _env_value(key, file_values, default)
    if value is None:
        return None

    normalized = value.strip()
    if not normalized or normalized.lower() in {"none", "null"}:
        return None
    return normalized


def _bool_env_value(
    key: str,
    file_values: dict[str, str],
    default: bool,
) -> bool:
    value = _env_value(key, file_values)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


def _load_s3_config() -> S3Config:
    """Resolve S3 settings from the correct env file plus process env overrides."""
    environment = _normalize_env(
        os.getenv("ENVIRONMENT")
        or os.getenv("APP_ENV")
        or _BOOT_ENVIRONMENT
    )
    env_path = _env_file_for(environment)
    file_values = _BOOT_ENV_VALUES if env_path == _BOOT_ENV_FILE else _read_env_file(env_path)

    if env_path.exists() and env_path != _BOOT_ENV_FILE:
        load_dotenv(env_path, override=False)

    bucket_name = _optional_env_value("S3_BUCKET_NAME", file_values)
    if not bucket_name:
        raise ValueError(
            f"S3_BUCKET_NAME is required for {environment} storage configuration"
        )

    endpoint_url = _optional_env_value("S3_ENDPOINT_URL", file_values)
    access_key_id = _optional_env_value("AWS_ACCESS_KEY_ID", file_values)
    secret_access_key = _optional_env_value("AWS_SECRET_ACCESS_KEY", file_values)
    region = _optional_env_value("S3_REGION", file_values, "us-east-1") or "us-east-1"

    if bool(access_key_id) != bool(secret_access_key):
        raise ValueError(
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be configured together"
        )

    if endpoint_url and not (access_key_id and secret_access_key):
        raise ValueError(
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are required for S3_ENDPOINT_URL"
        )

    create_bucket_default = not _is_production(environment)
    create_bucket = _bool_env_value(
        "S3_CREATE_BUCKET",
        file_values,
        create_bucket_default,
    )

    return S3Config(
        environment=environment,
        bucket_name=bucket_name,
        region=region,
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        create_bucket=create_bucket,
    )


class S3Client:
    """S3-compatible storage client for document storage."""

    def __init__(self):
        cfg = _load_s3_config()

        self.bucket_name = cfg.bucket_name
        self.endpoint_url = cfg.endpoint_url
        self.region = cfg.region
        self.environment = cfg.environment
        self.create_bucket = cfg.create_bucket

        # Configure S3 client
        config_kwargs = {
            'signature_version': 's3v4',
            'retries': {'max_attempts': 3, 'mode': 'standard'},
            'connect_timeout': 5,
            'read_timeout': 60,
        }
        if self.endpoint_url:
            config_kwargs['s3'] = {'addressing_style': 'path'}
        config = Config(**config_kwargs)

        client_kwargs = {
            'region_name': self.region,
            'config': config,
        }

        if cfg.access_key_id and cfg.secret_access_key:
            client_kwargs['aws_access_key_id'] = cfg.access_key_id
            client_kwargs['aws_secret_access_key'] = cfg.secret_access_key

        # Only pass endpoint_url if it's set (for S3-compatible services like MinIO/R2)
        if self.endpoint_url:
            client_kwargs['endpoint_url'] = self.endpoint_url

        self.client = boto3.client('s3', **client_kwargs)

        # Ensure bucket exists
        self._ensure_bucket()

    def _ensure_bucket(self):
        """Ensure the bucket exists."""
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
        except ClientError as e:
            error_code = str(e.response.get('Error', {}).get('Code', ''))
            if error_code not in {'404', 'NoSuchBucket', 'NotFound'}:
                raise RuntimeError(
                    f"Unable to access S3 bucket '{self.bucket_name}': {error_code}"
                ) from e

            if not self.create_bucket:
                raise RuntimeError(
                    f"S3 bucket '{self.bucket_name}' does not exist or is not accessible. "
                    "Create it before starting the service or set S3_CREATE_BUCKET=true."
                ) from e

            create_kwargs = {'Bucket': self.bucket_name}
            if not self.endpoint_url and self.region != 'us-east-1':
                create_kwargs['CreateBucketConfiguration'] = {
                    'LocationConstraint': self.region
                }

            try:
                self.client.create_bucket(**create_kwargs)
                self.client.get_waiter('bucket_exists').wait(Bucket=self.bucket_name)
            except ClientError as create_error:
                create_code = str(create_error.response.get('Error', {}).get('Code', ''))
                if create_code in {'BucketAlreadyOwnedByYou', 'BucketAlreadyExists'}:
                    return
                raise RuntimeError(
                    f"Unable to create S3 bucket '{self.bucket_name}': {create_code}"
                ) from create_error

    def upload_file(
        self,
        file_bytes: bytes,
        path: str,
        content_type: Optional[str] = None,
        metadata: Optional[dict] = None
    ) -> str:
        """Upload file to S3.

        Args:
            file_bytes: File content as bytes
            path: S3 object key
            content_type: MIME type
            metadata: Custom metadata

        Returns:
            S3 object key
        """
        extra_args = {}

        if content_type:
            extra_args['ContentType'] = content_type

        if metadata:
            extra_args['Metadata'] = {
                str(key): str(value)
                for key, value in metadata.items()
                if value is not None
            }

        self.client.upload_fileobj(
            io.BytesIO(file_bytes),
            self.bucket_name,
            path,
            ExtraArgs=extra_args
        )

        return path

    def upload_file_stream(
        self,
        file_stream: BinaryIO,
        path: str,
        content_type: Optional[str] = None,
        metadata: Optional[dict] = None
    ) -> str:
        """Upload file stream to S3.

        Args:
            file_stream: File-like object
            path: S3 object key
            content_type: MIME type
            metadata: Custom metadata

        Returns:
            S3 object key
        """
        extra_args = {}

        if content_type:
            extra_args['ContentType'] = content_type

        if metadata:
            extra_args['Metadata'] = {
                str(key): str(value)
                for key, value in metadata.items()
                if value is not None
            }

        self.client.upload_fileobj(
            file_stream,
            self.bucket_name,
            path,
            ExtraArgs=extra_args
        )

        return path

    def download_file(self, path: str) -> bytes:
        """Download file from S3.

        Args:
            path: S3 object key

        Returns:
            File content as bytes
        """
        buffer = io.BytesIO()
        self.client.download_fileobj(self.bucket_name, path, buffer)
        buffer.seek(0)
        return buffer.read()

    def generate_presigned_url(
        self,
        path: str,
        expiration: int = 3600,
        as_attachment: bool = False
    ) -> str:
        """Generate presigned URL for file access.

        Args:
            path: S3 object key
            expiration: URL expiration in seconds
            as_attachment: Force download

        Returns:
            Presigned URL
        """
        params = {'Bucket': self.bucket_name, 'Key': path}

        if as_attachment:
            params['ResponseContentDisposition'] = (
                f'attachment; filename="{path.split("/")[-1]}"'
            )

        return self.client.generate_presigned_url(
            'get_object',
            Params=params,
            ExpiresIn=expiration
        )

    def delete_file(self, path: str) -> bool:
        """Delete file from S3.

        Args:
            path: S3 object key

        Returns:
            True if deleted successfully
        """
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=path)
            return True
        except ClientError:
            return False

    def file_exists(self, path: str) -> bool:
        """Check if file exists in S3.

        Args:
            path: S3 object key

        Returns:
            True if file exists
        """
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=path)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            raise

    def get_file_metadata(self, path: str) -> dict:
        """Get file metadata from S3.

        Args:
            path: S3 object key

        Returns:
            File metadata
        """
        response = self.client.head_object(Bucket=self.bucket_name, Key=path)
        return {
            'content_type': response.get('ContentType'),
            'content_length': response.get('ContentLength'),
            'last_modified': response.get('LastModified'),
            'etag': response.get('ETag'),
            'metadata': response.get('Metadata', {}),
        }

    def list_files(
        self,
        prefix: str = '',
        max_keys: int = 1000
    ) -> list:
        """List files in S3 bucket.

        Args:
            prefix: Path prefix filter
            max_keys: Maximum number of keys to return

        Returns:
            List of file keys
        """
        response = self.client.list_objects_v2(
            Bucket=self.bucket_name,
            Prefix=prefix,
            MaxKeys=max_keys
        )

        return [obj['Key'] for obj in response.get('Contents', [])]

    @staticmethod
    def generate_path(
        workspace_id: str,
        document_id: str,
        filename: str
    ) -> str:
        """Generate S3 path for document.

        Args:
            workspace_id: Workspace UUID
            document_id: Document UUID
            filename: Original filename

        Returns:
            S3 object key
        """
        safe_filename = (
            "".join(c for c in filename if c.isalnum() or c in '._-').strip()
        )
        if not safe_filename:
            safe_filename = "document"
        return f"workspaces/{workspace_id}/documents/{document_id}/{safe_filename}"

    @staticmethod
    def compute_hash(file_bytes: bytes) -> str:
        """Compute SHA-256 hash of file content.

        Args:
            file_bytes: File content

        Returns:
            Hex digest of hash
        """
        return hashlib.sha256(file_bytes).hexdigest()


# Global S3 client instance - lazily initialized on first access to avoid
# crashes on import if S3 configuration is invalid.
_s3_client_instance = None


def get_s3_client() -> S3Client:
    """Get or create the global S3 client instance (lazy initialization)."""
    global _s3_client_instance
    if _s3_client_instance is None:
        _s3_client_instance = S3Client()
    return _s3_client_instance


# Backward compatibility: s3_client accessed as module attribute
class _S3ClientProxy:
    """Lazy proxy for s3_client to avoid initialization on import."""
    def __getattr__(self, name):
        return getattr(get_s3_client(), name)


s3_client = _S3ClientProxy()
