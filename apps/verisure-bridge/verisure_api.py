"""Minimal, read-only client for Verisure's unofficial app API (the one python-verisure/vsure uses).

It can only send the two read queries below; anything else is refused, so it cannot arm or disarm. The
bridge calls it once per arm/disarm push (and once at start), never on a timer, because the API rate-limits.

Login cookies are kept in COOKIE_FILE. If Verisure asks for a one-time code (MFA), run once:

    kubectl exec -it deploy/verisure-bridge -- python /app/verisure_api.py mfa-request
    kubectl exec -it deploy/verisure-bridge -- python /app/verisure_api.py mfa-validate <code>

after which the stored trust cookie lets later logins skip the code. `python verisure_api.py state` prints
the current arm state.
"""
import base64, http.cookiejar, json, os, sys, urllib.error, urllib.request

BASE_URLS = ["https://automation01.verisure.com", "https://automation02.verisure.com"]
HEADERS = {"APPLICATION_ID": "PS_PYTHON", "Accept": "application/json"}
COOKIE_FILE = os.environ.get("VERISURE_COOKIE_FILE", "/data/verisure-cookies.txt")
RATE_LIMIT_MARKERS = ("aut_00021", "acc_00002", "toomanystepuptokens", "request limit", "rate limit",
                      "too many requests")

INSTALLATIONS = ("query fetchAllInstallations($email: String!){\n  account(email: $email) {\n    installations {\n"
                 "      giid\n      alias\n      __typename\n    }\n    __typename\n  }\n}\n")
ARM_STATE = ("query ArmState($giid: String!) {\n  installation(giid: $giid) {\n    armState {\n      type\n"
             "      statusType\n      date\n      name\n      changedVia\n      __typename\n    }\n"
             "    __typename\n  }\n}\n")
ALLOWED_QUERIES = {"fetchAllInstallations": INSTALLATIONS, "ArmState": ARM_STATE}


class MfaRequired(Exception):
    pass


class RateLimited(Exception):
    pass


class Client:
    def __init__(self, username, password, cookie_file=COOKIE_FILE):
        self.username, self.password = username, password
        self.jar = http.cookiejar.LWPCookieJar(cookie_file)
        if os.path.exists(cookie_file):
            self.jar.load(ignore_discard=True, ignore_expires=True)
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.giid = None

    def _save(self):
        os.makedirs(os.path.dirname(self.jar.filename), exist_ok=True)
        self.jar.save(ignore_discard=True, ignore_expires=True)

    def _call(self, method, path, body=None, auth=False, content_type=None):
        """Try both API hosts; return (status, text)."""
        last = None
        for base in BASE_URLS:
            headers = dict(HEADERS)
            if auth:
                token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
                headers["Authorization"] = f"Basic {token}"
            if content_type:
                headers["Content-Type"] = content_type
            req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
            try:
                with self.opener.open(req, timeout=30) as r:
                    status, text = r.status, r.read().decode()
            except urllib.error.HTTPError as e:
                status, text = e.code, e.read().decode(errors="replace")
            except urllib.error.URLError as e:
                last = e
                continue
            if any(m in text.lower() for m in RATE_LIMIT_MARKERS) or status == 429:
                raise RateLimited(text[:200])
            if status >= 500 or "SYS_00004" in text:  # this host is unhappy, try the other
                last = RuntimeError(f"{status} {text[:200]}")
                continue
            self._save()
            return status, text
        raise RuntimeError(f"Verisure API unreachable: {last}")

    def login(self):
        status, text = self._call("POST", "/auth/login", auth=True)
        if "stepUpToken" in text:
            raise MfaRequired("Verisure wants a one-time code: run `verisure_api.py mfa-request`")
        if status != 200:
            raise RuntimeError(f"login failed: {status} {text[:200]}")

    def refresh(self):
        status, text = self._call("GET", "/auth/token")
        return status == 200

    def _query(self, name, variables):
        query = ALLOWED_QUERIES[name]  # KeyError for anything but the read queries above
        assert query.lstrip().startswith("query ") and "mutation" not in query
        body = json.dumps([{"operationName": name, "variables": variables, "query": query}]).encode()
        status, text = self._call("POST", "/graphql", body=body, content_type="application/json")
        data = json.loads(text) if text.strip().startswith(("[", "{")) else {"errors": text}
        if isinstance(data, list):
            data = data[0]
        return status, data

    def _query_with_login(self, name, variables):
        for step in ("as is", "refresh", "login"):
            if step == "refresh":
                self.refresh()
            elif step == "login":
                self.login()
            status, data = self._query(name, variables)
            if status == 200 and "errors" not in data:
                return data["data"]
        raise RuntimeError(f"{name} failed: {status} {str(data)[:200]}")

    def arm_state(self):
        """{'statusType': 'ARMED_HOME'|'ARMED_AWAY'|'DISARMED', 'date', 'name', 'changedVia', ...}"""
        if not self.giid:
            data = self._query_with_login("fetchAllInstallations", {"email": self.username})
            self.giid = data["account"]["installations"][0]["giid"]
        return self._query_with_login("ArmState", {"giid": self.giid})["installation"]["armState"]

    def mfa_request(self):
        status, text = self._call("POST", "/auth/login", auth=True)
        if "stepUpToken" not in text:
            return "no code needed: login works without MFA"
        for kind in ("phone", "email"):
            status, _ = self._call("POST", f"/auth/mfa?type={kind}")
            if status == 200:
                return f"code sent by {kind}"
        raise RuntimeError("could not request a code")

    def mfa_validate(self, code):
        self._call("POST", "/auth/mfa/validate", body=json.dumps({"token": code}).encode(),
                   content_type="application/json")
        self._call("POST", "/auth/trust")
        return "trusted; later logins skip the code"


def from_env():
    user, pw = os.environ.get("VERISURE_USERNAME"), os.environ.get("VERISURE_PASSWORD")
    return Client(user, pw) if user and pw else None


if __name__ == "__main__":
    c = from_env()
    if not c:
        sys.exit("VERISURE_USERNAME and VERISURE_PASSWORD are not set")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "state"
    if cmd == "mfa-request":
        print(c.mfa_request())
    elif cmd == "mfa-validate":
        print(c.mfa_validate(sys.argv[2]))
    else:
        print(json.dumps(c.arm_state(), ensure_ascii=False))
