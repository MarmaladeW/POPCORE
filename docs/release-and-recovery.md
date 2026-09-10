# Release and recovery

## Physical opening and cutover

For each store and location, approve `inventory_access`, verify product identity, native unit and any set conversion, then open a quiet write window. Count physical stock by product, location and disposition. Post the reviewed opening document with provenance and reconcile its movements to balances before enabling authoritative mode. Clover stock is reference material and is never trusted as opening stock. Unknown identity, unit, conversion, duplicate source or tender facts remain unresolved and block unsafe posting.

## Complete backup rehearsal

From `D:\dev\POPCORE`, run:

```powershell
.\.venv\Scripts\python.exe scripts\check_release_recovery.py --output .local/build6/recovery-new
```

The output directory must be new and under repository `.local`. The check performs SQLite online backup while a write commits, packages product images plus private payment and condition evidence referenced by the snapshot, hashes every byte, restores to another new directory, and runs integrity and foreign-key checks. It also proves missing and corrupt attachment packages fail. Credentials and `.env` values are never packaged.

Production `backup.sh` uses the same package helper. Configure database and three attachment roots through the `POPCORE_*` environment values installed by the systemd template. Keep the existing retention value unless the owner approves a policy change. Off-host destination, encryption credentials, retention approval and a real isolated host restore remain operator-owned launch gates.

To restore, stop writes, verify the manifest and hashes with `restore_package`, restore into a new directory, apply credentials separately with owner-only permissions, run SQLite integrity and foreign-key checks, set private-directory ownership, and start the candidate against that restored path. Never expose private evidence as static files. Never overwrite the only existing database or attachment tree.

If new-schema data has been posted, rolling application files backward may be unsafe. Preserve the current database and package first, then use reviewed forward recovery or reconciliation rather than silently restoring an older database and losing writes.
