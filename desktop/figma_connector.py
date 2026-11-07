"""Account-isolated Figma cloud bridge with bounded MCP calls and write receipts."""
import asyncio
import hashlib
import json
import re
import secrets
import time

from aiohttp import ClientError, ClientSession, ClientTimeout

from services.velia_structured_output import strict_json_loads

ENDPOINT = "https://figma-console-mcp.southleft.com/mcp"
PROTOCOLS = {"2025-03-26", "2025-06-18", "2025-11-25"}
CANVAS = "return {fileName:figma.root.name,fileKey:figma.fileKey||null,pageId:figma.currentPage.id,pageName:figma.currentPage.name,selection:figma.currentPage.selection.map(n=>({id:n.id,name:n.name,type:n.type})),children:figma.currentPage.children.slice(0,40).map(n=>({id:n.id,name:n.name,type:n.type,x:n.x,y:n.y,width:n.width,height:n.height}))};"


class FigmaError(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def redact_credentials(value):
    sensitive = re.compile(r"(?:^|_)(?:access_token|refresh_token|api_key|client_secret|private_key|password|authorization|token)(?:$|_)", re.I)
    if isinstance(value, dict):
        credential_record = sensitive.search(str(value.get("key", value.get("name", ""))))
        return {key: "[скрыто]" if sensitive.search(key) or credential_record and key == "value"
                else redact_credentials(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_credentials(item) for item in value]
    return value


def public_result(result, token, *, redact=False):
    """Send text evidence, never credential-bearing errors or binary payloads."""
    content = []
    failed = result.get("isError") is True
    if isinstance(result.get("structuredContent"), (dict, list)):
        structured = redact_credentials(result["structuredContent"])
        content.append(json.dumps(structured, ensure_ascii=False).replace(token, "[скрыто]")[:24000])
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
            text = re.sub(r"figd_[A-Za-z0-9_-]+", "[скрыто]", item["text"].replace(token, "[скрыто]"))
            content.append(text[:16000])
            try:
                value = strict_json_loads(text)
                if isinstance(value, dict) and (value.get("success") is False or value.get("error")):
                    failed = True
                if redact:
                    content[-1] = json.dumps(redact_credentials(value), ensure_ascii=False)[:16000]
            except (ValueError, TypeError):
                pass
    return {"isError": failed, "text": "\n".join(content)[:24000]}


class FigmaConnector:
    def __init__(self, request=None, *, endpoint=ENDPOINT, pair_required=True):
        self.connections = {}
        self.request = request or self.http
        self.oauth = None
        self.endpoint = endpoint
        self.pair_required = pair_required

    async def http(self, headers, payload):
        try:
            async with ClientSession(timeout=ClientTimeout(total=45), trust_env=False) as client:
                async with client.post(self.endpoint, headers=headers, json=payload, allow_redirects=False) as response:
                    if response.status in {401, 403}:
                        raise FigmaError("figma_access_denied", 403)
                    if response.status not in {200, 202, 204}:
                        raise FigmaError("figma_unavailable", 503)
                    size, chunks = 0, []
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > 2 * 1024 * 1024:
                            raise FigmaError("figma_response_too_large", 502)
                        chunks.append(chunk)
                    if "id" not in payload:
                        return {}, dict(response.headers)
                    body = b"".join(chunks).decode()
                    if "text/event-stream" in response.headers.get("Content-Type", ""):
                        values = []
                        for frame in body.replace("\r\n", "\n").split("\n\n"):
                            data = "\n".join(line[5:].lstrip() for line in frame.split("\n") if line.startswith("data:"))
                            if data:
                                value = strict_json_loads(data)
                                if isinstance(value, dict) and value.get("id") == payload["id"]:
                                    values.append(value)
                        if len(values) != 1:
                            raise FigmaError("figma_protocol_error", 502)
                        return values[0], dict(response.headers)
                    return strict_json_loads(body), dict(response.headers)
        except (ClientError, TimeoutError, ValueError, UnicodeError):
            raise FigmaError("figma_unavailable", 503) from None

    async def rpc(self, connection, method, params=None, *, notification=False):
        payload = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = secrets.token_hex(12)
        if params is not None:
            payload["params"] = params
        headers = {"Authorization": "Bearer " + connection["token"],
                   "User-Agent": "VELIA/1.0",
                   "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if connection.get("session_id"):
            headers["Mcp-Session-Id"] = connection["session_id"]
        if connection.get("protocol"):
            headers["MCP-Protocol-Version"] = connection["protocol"]
        value, received = await self.request(headers, payload)
        session_id = received.get("Mcp-Session-Id") or received.get("mcp-session-id")
        if session_id:
            if not isinstance(session_id, str) or len(session_id) > 500 or any(ord(c) < 33 for c in session_id):
                raise FigmaError("figma_protocol_error", 502)
            connection["session_id"] = session_id
        if notification:
            return {}
        if (not isinstance(value, dict) or value.get("jsonrpc") != "2.0" or value.get("id") != payload["id"]
                or value.get("error") or not isinstance(value.get("result"), dict)):
            raise FigmaError("figma_protocol_error", 502)
        return value["result"]

    def prune(self):
        now = time.monotonic()
        for key, item in list(self.connections.items()):
            expired = item["expires"] < now or item.get("oauth_expires") is not None and item["oauth_expires"] <= time.time() + 30
            if expired and not item["lock"].locked():
                del self.connections[key]

    def get(self, owner, connection_id=None):
        self.prune()
        item = self.connections.get(owner)
        if not item or connection_id is not None and item["id"] != connection_id:
            raise FigmaError("figma_connect_required", 409)
        item["expires"] = time.monotonic() + 3600
        return item

    async def connect(self, owner, token, *, oauth_expires=None):
        pattern = r"[\x21-\x7e]{10,4096}" if oauth_expires is not None else r"figd_[A-Za-z0-9_-]{10,250}"
        if not isinstance(token, str) or not re.fullmatch(pattern, token):
            raise FigmaError("figma_invalid_token")
        self.prune()
        if owner in self.connections and self.connections[owner]["lock"].locked():
            raise FigmaError("figma_busy", 409)
        if len(self.connections) >= 256:
            raise FigmaError("figma_busy", 429)
        connection = {"id": secrets.token_hex(16), "token": token, "lock": asyncio.Lock(),
                      "expires": time.monotonic() + 3600, "ready": False, "receipts": {}, "runs": {},
                      "oauth_expires": oauth_expires}
        self.connections[owner] = connection
        async with connection["lock"]:
            try:
                initialized = await self.rpc(connection, "initialize", {"protocolVersion": "2025-03-26",
                    "capabilities": {}, "clientInfo": {"name": "VELIA", "version": "1.0"}})
                if initialized.get("protocolVersion") not in PROTOCOLS:
                    raise FigmaError("figma_protocol_error", 502)
                connection["protocol"] = initialized["protocolVersion"]
                await self.rpc(connection, "notifications/initialized", notification=True)
                tools, cursor = {}, None
                for _ in range(5):
                    result = await self.rpc(connection, "tools/list", {"cursor": cursor} if cursor else {})
                    for tool in result.get("tools", []):
                        name = tool.get("name") if isinstance(tool, dict) else None
                        pattern = r"figma_[a-z0-9_]{1,90}" if self.pair_required else r"[A-Za-z0-9_-]{1,100}"
                        if isinstance(name, str) and re.fullmatch(pattern, name) and isinstance(tool.get("inputSchema"), dict):
                            tools[name] = {"name": name, "description": str(tool.get("description", ""))[:4000], "inputSchema": tool["inputSchema"]}
                    if len(tools) > 200 or len(json.dumps(tools)) > 700000:
                        raise FigmaError("figma_protocol_error", 502)
                    cursor = result.get("nextCursor")
                    if not cursor:
                        break
                    if not isinstance(cursor, str) or len(cursor) > 2000:
                        raise FigmaError("figma_protocol_error", 502)
                else:
                    raise FigmaError("figma_protocol_error", 502)
                if not tools or self.pair_required and not {"figma_pair_plugin", "figma_execute"} <= tools.keys():
                    raise FigmaError("figma_protocol_error", 502)
                connection["tools"] = tools
                if not self.pair_required:
                    connection["ready"] = True
                    return {"connection_id": connection["id"], "connected": True}
                paired = await self.rpc(connection, "tools/call", {"name": "figma_pair_plugin", "arguments": {}})
                public = public_result(paired, token)
                data = strict_json_loads(public["text"])
                if public["isError"] or not isinstance(data, dict) or not re.fullmatch(r"[A-Z0-9]{6}", str(data.get("pairingCode", ""))):
                    raise FigmaError("figma_pairing_failed", 502)
                connection["pairing_code"] = data["pairingCode"]
                connection["pairing_until"] = time.time() + 300
                return {"connection_id": connection["id"], "pairing_code": data["pairingCode"], "expires_in": 300}
            except BaseException:
                if self.connections.get(owner) is connection:
                    self.connections.pop(owner, None)
                raise

    async def canvas(self, connection):
        if not self.pair_required:
            await self.rpc(connection, "ping")
            return {}
        result = await self.rpc(connection, "tools/call", {"name": "figma_execute", "arguments": {"code": CANVAS, "timeout": 5000}})
        public = public_result(result, connection["token"])
        try:
            evidence = strict_json_loads(public["text"])
        except (ValueError, TypeError):
            raise FigmaError("figma_plugin_required", 409) from None
        if public["isError"] or not isinstance(evidence, dict) or evidence.get("success") is not True or not isinstance(evidence.get("result"), dict):
            connection["ready"] = False
            raise FigmaError("figma_plugin_required", 409)
        canvas = evidence["result"]
        previous = connection.get("canvas")
        if previous and (previous.get("fileKey") or previous.get("fileName")) != (canvas.get("fileKey") or canvas.get("fileName")):
            connection["ready"] = False
            raise FigmaError("figma_file_changed", 409)
        return canvas

    async def verify(self, owner):
        connection = self.get(owner)
        if connection["lock"].locked():
            raise FigmaError("figma_busy", 409)
        async with connection["lock"]:
            canvas = await self.canvas(connection)
            connection["ready"] = True
            connection["canvas"] = canvas
            return self.status(owner)

    def status(self, owner):
        try:
            connection = self.get(owner)
        except FigmaError:
            return {"connected": False, "status": "disconnected"}
        return {"connected": connection["ready"], "status": "connected" if connection["ready"] else "pairing",
                "pairing_code": connection.get("pairing_code") if not connection["ready"] and connection.get("pairing_until", 0) > time.time() else None,
                "connection_id": connection["id"], "file_name": str(connection.get("canvas", {}).get("fileName", ""))[:200],
                "tool_count": len(connection.get("tools", {}))}

    def catalog(self, owner):
        connection = self.get(owner)
        if not connection["ready"]:
            raise FigmaError("figma_plugin_required", 409)
        return [tool for name, tool in connection["tools"].items() if name != "figma_pair_plugin"]

    async def call(self, owner, data):
        if not isinstance(data, dict) or set(data) != {"connection_id", "request_id", "run_id", "operation_id", "name", "arguments"}:
            raise FigmaError("figma_invalid_call")
        if (not isinstance(data["name"], str) or not isinstance(data["arguments"], dict)
                or not isinstance(data["operation_id"], str) or not re.fullmatch(r"[A-Za-z0-9:_-]{8,128}", data["operation_id"])):
            raise FigmaError("figma_invalid_call")
        if any(not isinstance(data[key], str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", data[key]) for key in ("request_id", "run_id")):
            raise FigmaError("figma_invalid_call")
        connection = self.get(owner, data["connection_id"])
        if not connection["ready"] or data["name"] not in {tool["name"] for tool in self.catalog(owner)}:
            raise FigmaError("figma_invalid_call")
        if connection["lock"].locked():
            raise FigmaError("figma_busy", 409)
        fingerprint = hashlib.sha256(json.dumps([data["name"], data["arguments"]], sort_keys=True, allow_nan=False).encode()).hexdigest()
        async with connection["lock"]:
            previous_run = connection["runs"].get(data["request_id"])
            if previous_run is not None and previous_run != data["run_id"]:
                raise FigmaError("figma_task_already_attempted", 409)
            receipt = connection["receipts"].get(data["operation_id"])
            if receipt:
                if receipt["fingerprint"] != fingerprint:
                    raise FigmaError("figma_operation_conflict", 409)
                if receipt.get("result") is None:
                    raise FigmaError("figma_execution_uncertain", 409)
                return receipt["result"]
            if len(connection["receipts"]) >= 1000:
                raise FigmaError("figma_reconnect_required", 409)
            await self.canvas(connection)
            connection["runs"][data["request_id"]] = data["run_id"]
            receipt = {"fingerprint": fingerprint}
            connection["receipts"][data["operation_id"]] = receipt
            try:
                result = await self.rpc(connection, "tools/call", {"name": data["name"], "arguments": data["arguments"]})
            except FigmaError:
                raise FigmaError("figma_execution_uncertain", 409) from None
            receipt["result"] = public_result(result, connection["token"], redact=not self.pair_required)
            return receipt["result"]

    def disconnect(self, owner):
        if self.oauth is not None:
            self.oauth.cancel(owner)
        self.connections.pop(owner, None)
