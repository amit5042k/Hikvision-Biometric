"""Validation of QR code contents and the vCard file served for contact codes."""
import re

# Allowed form fields per code type (names match the inputs in web/index.html).
FIELDS = {
    "url": ("url",),
    "text": ("text",),
    "wifi": ("ssid", "security", "wifiPassword", "hidden"),
    "vcard": ("firstName", "lastName", "vPhone", "vEmail", "org", "title", "vUrl", "address"),
    "email": ("emailTo", "emailSubject", "emailBody"),
    "sms": ("smsNumber", "smsBody"),
    "phone": ("phone",),
}
LONG_FIELDS = {"text", "emailBody", "smsBody"}
WIFI_SECURITY = ("WPA", "WEP", "nopass")
TYPE_NAMES = {"url": "Link", "text": "Text", "wifi": "Wi-Fi", "vcard": "Contact",
              "email": "Email", "sms": "SMS", "phone": "Phone"}


class ContentError(ValueError):
    pass


def clean(code_type, raw):
    """Returns the normalised field dict for a code, or raises ContentError."""
    if code_type not in FIELDS:
        raise ContentError("Unknown QR code type.")
    if not isinstance(raw, dict):
        raise ContentError("Missing QR code details.")
    data = {}
    for name in FIELDS[code_type]:
        value = raw.get(name, "")
        if name == "hidden":
            data[name] = bool(value)
            continue
        value = "" if value is None else str(value)
        if name not in LONG_FIELDS:
            value = value.strip()
        if len(value) > (4000 if name in LONG_FIELDS else 500):
            raise ContentError("A field is too long.")
        data[name] = value

    if code_type == "url":
        url = data["url"]
        if url and not re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I):
            url = "https://" + url
        if not re.match(r"^https?://[^\s/$.?#][^\s]*$", url, re.I):
            raise ContentError("Enter a valid website address (http:// or https://).")
        data["url"] = url
    elif code_type == "text" and not data["text"].strip():
        raise ContentError("Enter some text.")
    elif code_type == "wifi":
        if not data["ssid"]:
            raise ContentError("Enter the network name.")
        if data["security"] not in WIFI_SECURITY:
            data["security"] = "WPA"
    elif code_type == "vcard" and not any(data[k] for k in ("firstName", "lastName", "vPhone", "vEmail", "org")):
        raise ContentError("Enter at least a name, phone, email or company.")
    elif code_type == "email":
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", data["emailTo"]):
            raise ContentError("Enter a valid email address.")
    elif code_type == "sms":
        data["smsNumber"] = phone_digits(data["smsNumber"])
        if not data["smsNumber"]:
            raise ContentError("Enter a phone number.")
    elif code_type == "phone":
        data["phone"] = phone_digits(data["phone"])
        if not data["phone"]:
            raise ContentError("Enter a phone number.")
    return data


def phone_digits(s):
    return re.sub(r"[^\d+]", "", s)


def default_label(code_type, data):
    first = {
        "url": lambda: re.sub(r"^https?://(www\.)?", "", data["url"]),
        "text": lambda: data["text"].strip().splitlines()[0],
        "wifi": lambda: data["ssid"],
        "vcard": lambda: (data["firstName"] + " " + data["lastName"]).strip() or data["org"] or data["vPhone"],
        "email": lambda: data["emailTo"],
        "sms": lambda: data["smsNumber"],
        "phone": lambda: data["phone"],
    }[code_type]()
    return (first or TYPE_NAMES[code_type])[:80]


def _esc(s):
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def vcard(data):
    first, last = data["firstName"], data["lastName"]
    lines = ["BEGIN:VCARD", "VERSION:3.0",
             f"N:{_esc(last)};{_esc(first)};;;",
             f"FN:{_esc((first + ' ' + last).strip() or data['org'])}"]
    for key, prop in (("org", "ORG"), ("title", "TITLE"), ("vPhone", "TEL;TYPE=CELL"),
                      ("vEmail", "EMAIL"), ("vUrl", "URL")):
        if data[key]:
            lines.append(f"{prop}:{_esc(data[key])}")
    if data["address"]:
        lines.append(f"ADR:;;{_esc(data['address'])};;;;")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"
