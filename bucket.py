from google.cloud import storage
from google.oauth2 import service_account
import sys

def check_bucket_access(key_path: str, project_id: str, bucket_name: str) -> bool:
    """
    Returns True if the service account (from key_path) can access the bucket.
    """
    try:
        # Load credentials from your service‐account JSON key
        creds = service_account.Credentials.from_service_account_file(
            key_path
        )
        # Instantiate a client with those creds
        client = storage.Client(project=project_id, credentials=creds)
        
        # Try to get the bucket metadata
        bucket = client.get_bucket(bucket_name)  
        print(f"✅ Bucket `{bucket.name}` is accessible.")
        
        # Or, to double‐check list‐permissions, try listing a few blobs:
        blobs = list(bucket.list_blobs(max_results=1))
        print(f"✅ Able to list objects in `{bucket.name}` (found {len(blobs)}).")
        
        return True

    except Exception as e:
        print(f"❌ Failed to access bucket `{bucket_name}`: {e}", file=sys.stderr)
        return False

from google.cloud import storage
from google.oauth2 import service_account
import sys
import os

def grant_bucket_access(key_path: str, project_id: str, bucket_name: str, user_email: str, role: str = "roles/storage.objectViewer"):
    try:
        # Load service account credentials
        creds = service_account.Credentials.from_service_account_file(key_path)
        client = storage.Client(project=project_id, credentials=creds)

        bucket = client.bucket(bucket_name)
        policy = bucket.get_iam_policy(requested_policy_version=3)

        binding_found = False
        for binding in policy.bindings:
            if binding["role"] == role:
                if f"user:{user_email}" in binding["members"]:
                    print(f"ℹ️  {user_email} already has {role} access.")
                    return True
                binding["members"].add(f"user:{user_email}")
                binding_found = True
                break

        if not binding_found:
            policy.bindings.append({
                "role": role,
                "members": {f"user:{user_email}"}
            })

        bucket.set_iam_policy(policy)
        print(f"✅ Granted {role} to {user_email} on bucket `{bucket_name}`.")
        return True

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    SERVICE_ACCOUNT_KEY = "bucket_cred.json"
    PROJECT_ID = "fluent-oarlock-461903-h3"
    BUCKET_NAME = "nashermiles2"
    EMAIL = "kpatel@nashermiles.com"  # Change this or read from a list

    success = grant_bucket_access(SERVICE_ACCOUNT_KEY, PROJECT_ID, BUCKET_NAME, EMAIL)
    sys.exit(0 if success else 1)

# if __name__ == "__main__":
#     # Replace these with your values:
#     SERVICE_ACCOUNT_KEY = "bucket_cred.json"
#     PROJECT_ID            = "fluent-oarlock-461903-h3"
#     BUCKET_NAME           = "nashermiles2"

#     ok = check_bucket_access(SERVICE_ACCOUNT_KEY, PROJECT_ID, BUCKET_NAME)
#     sys.exit(0 if ok else 1)
