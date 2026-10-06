# Key rotation

## Encryption key

`SECRET_ENCRYPTION_KEY` encrypts every stored secret: data source passwords,
repository tokens, SSO client secrets. Rotation is online — the portal accepts
several keys at once so nothing is unreadable mid-rotation.

```bash
# 1. Put the new key first, keep the old one after it
#    SECRET_ENCRYPTION_KEY=<new>,<old>
docker compose up -d

# 2. Re-encrypt everything with the new key
docker compose exec web python manage.py rotate_encryption

# 3. Drop the old key
#    SECRET_ENCRYPTION_KEY=<new>
docker compose up -d
```

Do not skip step 3: leaving the old key configured means a leaked old key
still decrypts your data.

## Session signing key

`SESSION_SECRET_KEY` signs session cookies and the one-time links in invitation
and password-reset email. It can be rotated freely: everyone signs in again and
any unused invitation or reset link stops working — no other effect.

## When to rotate

- On any suspicion that a key leaked (a `.env` committed, a backup mishandled)
- When someone with access to the host leaves
- On whatever schedule your security policy dictates

There is no forced rotation and no expiry: rotation is an operator decision.
