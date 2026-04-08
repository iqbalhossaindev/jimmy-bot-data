# JIMMY Bot Source Code v2

This version adds:

1. Private signup
2. Admin approve or reject
3. Automatic group join request link after approval
4. Automatic approval of group join requests
5. Login report to private group and user inbox
6. Logout and DSR report to private group and user inbox
7. Status with monthly totals and date wise product names
8. Admin history search

## Important setup

Your bot must be admin in the private group with permission to invite users.
The bot must stay in the group to receive join request updates.

## How to use

1. Copy `.env.example` to `.env`
2. Fill real values
3. Install dependencies
4. Run:

```bash
pip install -r requirements.txt
python main.py
```
