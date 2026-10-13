"""Minimal Hikvision ISAPI client for face/card access terminals (e.g. DS-K1T320EFWX).

All calls use HTTP digest authentication, which is what the terminals require
out of the box. Only the endpoints needed for attendance are wrapped.
"""
import json
import uuid
from datetime import datetime
from xml.sax.saxutils import escape

import requests
import urllib3
from requests.auth import HTTPDigestAuth

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PAGE_SIZE = 30  # most terminals cap maxResults at 30


class HikvisionError(Exception):
    pass


def parse_device_time(value):
    """'2024-05-01T09:01:02+05:30' -> '2024-05-01 09:01:02' (device local time)."""
    if not value:
        return None
    value = value.strip()
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def format_device_time(value, tz_offset):
    """'2024-05-01 09:00:00' -> '2024-05-01T09:00:00+05:30'."""
    if isinstance(value, datetime):
        value = value.strftime("%Y-%m-%d %H:%M:%S")
    return value.replace(" ", "T")[:19] + (tz_offset or "")


class HikvisionClient:
    def __init__(self, host, port=80, username="admin", password="", scheme="http",
                 tz_offset="+05:30", door_no=1, timeout=15, session=None):
        self.base = f"{scheme}://{host}:{port}"
        self.auth = HTTPDigestAuth(username, password)
        self.tz_offset = tz_offset
        self.door_no = door_no
        self.timeout = timeout
        self.session = session or requests.Session()
        # Terminals sit on the LAN and commonly use self-signed certificates:
        # skip system proxies and certificate verification for them.
        self.session.trust_env = False
        self.session.verify = False

    @classmethod
    def from_row(cls, row, **kwargs):
        return cls(row["host"], row["port"], row["username"], row["password"], row["scheme"],
                   row["tz_offset"], row["door_no"], **kwargs)

    # -- transport ---------------------------------------------------------
    def _request(self, method, path, **kwargs):
        url = self.base + path
        try:
            resp = self.session.request(method, url, auth=self.auth, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise HikvisionError(f"Cannot reach device at {self.base}: {exc}") from exc
        if resp.status_code == 401:
            raise HikvisionError("Authentication failed - check device username/password")
        if resp.status_code >= 400:
            raise HikvisionError(f"{method} {path} failed ({resp.status_code}): {_error_text(resp)}")
        return resp

    def _json(self, method, path, payload=None):
        sep = "&" if "?" in path else "?"
        resp = self._request(method, f"{path}{sep}format=json",
                             data=json.dumps(payload) if payload is not None else None,
                             headers={"Content-Type": "application/json"})
        try:
            data = resp.json()
        except ValueError:
            return {}
        if isinstance(data, dict) and data.get("statusCode") not in (None, 1) and data.get("statusString") != "OK":
            raise HikvisionError(f"{path}: {data.get('subStatusCode') or data.get('statusString')}")
        return data

    # -- system --------------------------------------------------------------
    def device_info(self):
        text = self._request("GET", "/ISAPI/System/deviceInfo").text
        return _xml_fields(text, ["deviceName", "model", "serialNumber", "macAddress",
                                  "firmwareVersion", "firmwareReleasedDate", "deviceType"])

    def get_time(self):
        text = self._request("GET", "/ISAPI/System/time").text
        return _xml_fields(text, ["timeMode", "localTime", "timeZone"])

    def set_time(self, local_dt):
        body = ("<?xml version='1.0' encoding='UTF-8'?>"
                "<Time xmlns='http://www.isapi.org/ver20/XMLSchema' version='2.0'>"
                "<timeMode>manual</timeMode>"
                f"<localTime>{format_device_time(local_dt, self.tz_offset)}</localTime>"
                "</Time>")
        self._request("PUT", "/ISAPI/System/time", data=body,
                      headers={"Content-Type": "application/xml"})

    def open_door(self, door_no=None):
        body = "<RemoteControlDoor><cmd>open</cmd></RemoteControlDoor>"
        self._request("PUT", f"/ISAPI/AccessControl/RemoteControl/door/{door_no or self.door_no}",
                      data=body, headers={"Content-Type": "application/xml"})

    def set_http_host(self, ip, port, url_path, host_id=1):
        """Point the device's event push ("HTTP listening") at this server."""
        body = ("<?xml version='1.0' encoding='UTF-8'?>"
                "<HttpHostNotificationList xmlns='http://www.isapi.org/ver20/XMLSchema' version='2.0'>"
                "<HttpHostNotification>"
                f"<id>{host_id}</id><url>{escape(url_path)}</url>"
                "<protocolType>HTTP</protocolType><parameterFormatType>JSON</parameterFormatType>"
                f"<addressingFormatType>ipaddress</addressingFormatType><ipAddress>{escape(ip)}</ipAddress>"
                f"<portNo>{int(port)}</portNo><httpAuthenticationMethod>none</httpAuthenticationMethod>"
                "</HttpHostNotification></HttpHostNotificationList>")
        self._request("PUT", "/ISAPI/Event/notification/httpHosts", data=body,
                      headers={"Content-Type": "application/xml"})

    # -- users ---------------------------------------------------------------
    def iter_users(self):
        search_id = uuid.uuid4().hex
        position = 0
        while True:
            data = self._json("POST", "/ISAPI/AccessControl/UserInfo/Search", {
                "UserInfoSearchCond": {"searchID": search_id, "searchResultPosition": position,
                                       "maxResults": PAGE_SIZE}})
            result = data.get("UserInfoSearch", {})
            users = result.get("UserInfo") or []
            yield from users
            position += len(users)
            if result.get("responseStatusStrg") != "MORE" or not users:
                break

    def _user_payload(self, employee_no, name, gender=None, begin=None, end=None):
        user = {
            "employeeNo": str(employee_no),
            "name": name[:32],
            "userType": "normal",
            "Valid": {"enable": True,
                      "beginTime": begin or "2020-01-01T00:00:00",
                      "endTime": end or "2037-12-31T23:59:59",
                      "timeType": "local"},
            "doorRight": str(self.door_no),
            "RightPlan": [{"doorNo": self.door_no, "planTemplateNo": "1"}],
        }
        if gender in ("male", "female"):
            user["gender"] = gender
        return {"UserInfo": user}

    def upsert_user(self, employee_no, name, gender=None):
        """Create the user, or update it if it already exists on the device."""
        payload = self._user_payload(employee_no, name, gender)
        try:
            self._json("POST", "/ISAPI/AccessControl/UserInfo/Record", payload)
        except HikvisionError as exc:
            if "AlreadyExist" not in str(exc):
                raise
            self._json("PUT", "/ISAPI/AccessControl/UserInfo/Modify", payload)

    def delete_user(self, employee_no):
        self._json("PUT", "/ISAPI/AccessControl/UserInfoDetail/Delete", {
            "UserInfoDetail": {"mode": "byEmployeeNo",
                               "EmployeeNoList": [{"employeeNo": str(employee_no)}]}})

    def set_card(self, employee_no, card_no):
        payload = {"CardInfo": {"employeeNo": str(employee_no), "cardNo": str(card_no),
                                "cardType": "normalCard"}}
        try:
            self._json("POST", "/ISAPI/AccessControl/CardInfo/Record", payload)
        except HikvisionError as exc:
            if "AlreadyExist" not in str(exc):
                raise
            self._json("PUT", "/ISAPI/AccessControl/CardInfo/Modify", payload)

    def upload_face(self, employee_no, jpeg_bytes):
        record = {"faceLibType": "blackFD", "FDID": "1", "FPID": str(employee_no)}
        files = {
            "FaceDataRecord": (None, json.dumps(record), "application/json"),
            "img": ("face.jpg", jpeg_bytes, "image/jpeg"),
        }
        resp = self._request("PUT", "/ISAPI/Intelligent/FDLib/FDSetUp?format=json", files=files)
        data = _safe_json(resp)
        if data.get("statusCode") not in (None, 1):
            raise HikvisionError(f"Face upload failed: {data.get('subStatusCode') or data.get('statusString')}")

    # -- events --------------------------------------------------------------
    def iter_events(self, start, end, minors=(75,)):
        """Yield access-control events between two device-local datetimes.

        Each yielded dict is the raw ISAPI record (``employeeNoString``, ``time``,
        ``major``, ``minor``, ``serialNo``, ``currentVerifyMode``...).
        """
        for minor in minors:
            search_id = uuid.uuid4().hex
            position = 0
            while True:
                data = self._json("POST", "/ISAPI/AccessControl/AcsEvent", {
                    "AcsEventCond": {
                        "searchID": search_id,
                        "searchResultPosition": position,
                        "maxResults": PAGE_SIZE,
                        "major": 5,
                        "minor": int(minor),
                        "startTime": format_device_time(start, self.tz_offset),
                        "endTime": format_device_time(end, self.tz_offset),
                    }})
                result = data.get("AcsEvent", {})
                events = result.get("InfoList") or []
                yield from events
                position += len(events)
                if result.get("responseStatusStrg") != "MORE" or not events:
                    break


def _safe_json(resp):
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _error_text(resp):
    data = _safe_json(resp)
    if data:
        return data.get("subStatusCode") or data.get("statusString") or resp.text[:200]
    fields = _xml_fields(resp.text, ["subStatusCode", "statusString"])
    return fields.get("subStatusCode") or fields.get("statusString") or resp.text[:200]


def _xml_fields(text, names):
    """Pull simple leaf values out of an ISAPI XML document, ignoring namespaces."""
    import xml.etree.ElementTree as ET
    out = {}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return out
    for el in root.iter():
        tag = el.tag.split("}", 1)[-1]
        if tag in names and tag not in out:
            out[tag] = (el.text or "").strip()
    return out
