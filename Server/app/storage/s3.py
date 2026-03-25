"""S3-compatible storage client."""
import io
from typing import Optional, BinaryIO
from datetime import timedelta
import hashlib

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings


class S3Client:
    """S3-compatible storage client for document storage."""
    
    def __init__(self):
        self.bucket_name = settings.S3_BUCKET_NAME
        self.endpoint_url = settings.S3_ENDPOINT_URL
        
        # Configure S3 client
        config = Config(
            signature_version='s3v4',
            retries={'max_attempts': 3, 'mode': 'standard'}
        )
        
        self.client = boto3.client(
            's3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.S3_REGION,
            config=config,
        )
        
        # Ensure bucket exists
        self._ensure_bucket()
    
    def _ensure_bucket(self):
        """Ensure the bucket exists."""
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                self.client.create_bucket(Bucket=self.bucket_name)
            else:
                raise
    
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
            extra_args['Metadata'] = metadata
        
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
            extra_args['Metadata'] = metadata
        
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
            params['ResponseContentDisposition'] = f'attachment; filename="{path.split("/")[-1]}"'
        
        url = self.client.generate_presigned_url(
            'get_object',
            Params=params,
            ExpiresIn=expiration
        )
        
        return url
    
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
        # Clean filename
        safe_filename = "".join(c for c in filename if c.isalnum() or c in '._-').strip()
        
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


# Global S3 client instance
s3_client = S3Client()
