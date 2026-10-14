"""Entry point for GoDaddy cPanel "Setup Python App" (Phusion Passenger).

Data (database, session key, backups) is kept OUTSIDE the code folder, in
~/attendance-data by default, so updating the code with git can never reset
logins or attendance records. Override with the ATTENDANCE_DATA_DIR variable
in the cPanel Python App screen if you want a different place.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

os.environ.setdefault("ATTENDANCE_DATA_DIR", os.path.join(os.path.expanduser("~"), "attendance-data"))
os.environ.setdefault("BEHIND_PROXY", "1")

from app import create_app  # noqa: E402

application = create_app()
