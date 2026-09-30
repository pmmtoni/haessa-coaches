"""Run locally once to authorize Drive and create the dedicated archive folder."""
import argparse
import json
import os
from pathlib import Path
from audit_drive import DriveStore, SCOPE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--client', required=True, type=Path, help='Downloaded Desktop OAuth client JSON')
    parser.add_argument('--output', required=True, type=Path, help='Private .env file OUTSIDE your Git repository')
    parser.add_argument('--folder-id', help='Existing folder ID when refreshing an existing authorization')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists. Use a new private filename; existing secrets will not be overwritten.')
    output = args.output.resolve()
    if any((parent / '.git').exists() for parent in (output.parent, *output.parent.parents)):
        parser.error('Store the private .env file outside your Git repository.')
    with args.client.open(encoding='utf-8-sig') as f:
        client = json.load(f)
    if 'installed' not in client:
        parser.error('Create a Desktop app OAuth client (not a Web application client).')
    # Imported only by the one-time setup, not the scheduled archive job.
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_config(client, [SCOPE])
    credentials = flow.run_local_server(port=0, access_type='offline', prompt='consent',
        authorization_prompt_message='Your browser will open for Google authorization.',
        success_message='Google Drive authorization completed. You can close this tab.')
    if not credentials.refresh_token:
        raise RuntimeError('Google did not return a refresh token. Repeat setup and grant consent.')
    store = DriveStore(credentials.client_id, credentials.client_secret,
                       credentials.refresh_token, args.folder_id)
    folder = store.check_folder() if args.folder_id else store.create_folder()
    settings = {
        'AUDIT_GOOGLE_CLIENT_ID': credentials.client_id,
        'AUDIT_GOOGLE_CLIENT_SECRET': credentials.client_secret,
        'AUDIT_GOOGLE_REFRESH_TOKEN': credentials.refresh_token,
        'AUDIT_DRIVE_FOLDER_ID': folder['id'],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    # Never print tokens or write them into the application repository.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        for key, value in settings.items():
            if '\n' in value or '\r' in value:
                raise RuntimeError('Unexpected newline in a Google credential.')
            f.write(f'{key}={value}\n')
    print('Google Drive archive folder created/verified: ' + folder['name'])
    print('Folder ID: ' + folder['id'])
    if folder.get('webViewLink'):
        print('Folder: ' + folder['webViewLink'])
    print('Private Render settings saved to: ' + str(output))
    print('Copy these four settings into the Render CRON JOB environment. Do not commit or share the file.')


if __name__ == '__main__':
    main()
