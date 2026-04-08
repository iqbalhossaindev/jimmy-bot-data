# JIMMY Bot Final Package

This package includes:

1. Private signup flow
2. Admin approve or reject
3. Login report to private group and user inbox
4. Logout flow with:
   1. sales entry
   2. Reset button
   3. Undo button
   4. input validation
5. Status report with:
   1. working days
   2. total hours
   3. total sales
   4. total quantity
   5. total break time
   6. date wise products
6. Break system:
   1. Break Start
   2. Break End
7. Monthly PDF timesheet style
8. Admin history text and PDF
9. Daily absence warning to staff and admin
10. Automatic monthly report message
11. Render health server for web service deployment

## Required repo files
Keep these in the same repo root:
- employees.json
- pending.json
- sales.json
- break.json
- absence.json
- attendance.csv

## Required environment variables
- BOT_TOKEN
- ADMIN_IDS
- PRIVATE_GROUP_ID
- GITHUB_TOKEN
- GITHUB_OWNER
- GITHUB_REPO
- GITHUB_BRANCH
- TIMEZONE
- BRAND_NAME

## Render
- Build command: `pip install -r requirements.txt`
- Start command: `python main.py`

## Logo
If you want the PDF to include your logo, upload one of these in the repo root:
- logo.png
- logo.jpg
- logo.jpeg
