# Protocol fixture provenance (T002)

Every file here is transcribed from the pinned vendor sources — `.research_stackchan/xiaozhi-esp32`
at tag `v2.2.4` (`e77dedb`), plus `.research_stackchan/StackChan/firmware/main/hal/hal_mcp.cpp` for the
robot's six tool descriptors. Nothing here is invented: no message type, field name or default value
was added by this repository. The shared `session_id` is the constant
`0123456789abcdef0123456789abcdef`; at runtime it is a `uuid4().hex` issued in the server `hello`
(plan D11), and the device echoes whatever it received.

Paths are relative to the vendor checkout named in each row.

| Fixture | Direction | Transcribed from |
|---|---|---|
| `hello_device.json` | device → server | `xiaozhi-esp32/main/protocols/websocket_protocol.cc:203-227` (`GetHelloMessage`), `docs/websocket.md` §4.1.1 |
| `hello_server.json` | server → device | `xiaozhi-esp32/docs/websocket.md` §1.4 and §4.2.1 (server `hello`, `session_id`, 24 kHz `audio_params`) |
| `listen_start.json` | device → server | `xiaozhi-esp32/main/protocols/protocol.cc:57-69` (`SendStartListening`; `mode` is one of `manual`/`auto`/`realtime`) |
| `listen_stop.json` | device → server | `xiaozhi-esp32/main/protocols/protocol.cc:71-75` (`SendStopListening`) |
| `listen_detect.json` | device → server | `xiaozhi-esp32/main/protocols/protocol.cc:51-55` (`SendWakeWordDetected`; `text` is the wake word) |
| `abort.json` | device → server | `xiaozhi-esp32/main/protocols/protocol.cc:42-50` (`SendAbortSpeaking`; `reason` only when `wake_word_detected`) |
| `stt.json` | server → device | `xiaozhi-esp32/docs/websocket.md` §4.2.2; parsed at `main/application.cc:550-557` |
| `llm.json` | server → device | `xiaozhi-esp32/docs/websocket.md` §4.2.3; `emotion` consumed at `main/application.cc:558-564` |
| `tts_start.json` | server → device | `xiaozhi-esp32/docs/websocket.md` §4.2.4; state handled at `main/application.cc:524-545` |
| `tts_sentence_start.json` | server → device | `xiaozhi-esp32/docs/websocket.md` §4.2.4 |
| `tts_stop.json` | server → device | `xiaozhi-esp32/docs/websocket.md` §4.2.4 |
| `mcp_initialize_request.json` | gateway → device | `xiaozhi-esp32/docs/mcp-protocol.md` §2 (payload envelope from `main/protocols/protocol.cc:77-80`) |
| `mcp_initialize_result.json` | device → gateway | `xiaozhi-esp32/main/mcp_server.cc:389-396` (`protocolVersion`, `capabilities.tools`, `serverInfo`) |
| `mcp_tools_list_result.json` | device → gateway | `xiaozhi-esp32/main/mcp_server.cc:454-509` (`GetToolsList`) + `main/mcp_server.h:232-262` (`McpTool::to_json`) + `StackChan/firmware/main/hal/hal_mcp.cpp:28-30` (name and description verbatim). `required` is omitted because the firmware only adds it when the property list is non-empty |
| `mcp_tools_call_request.json` | gateway → device | `xiaozhi-esp32/docs/mcp-protocol.md` §4 |
| `mcp_tools_call_result.json` | device → gateway | `xiaozhi-esp32/main/mcp_server.h:271-303` (`McpTool::Call`) + `StackChan/firmware/main/hal/hal_mcp.cpp:37` (returned text) |
| `ota_response.json` | gateway → device | plan C1 (`specs/001-jarvis-gateway/plan.md:181-202`) + `xiaozhi-esp32/main/ota.cc:112-240`; `timestamp` is epoch **milliseconds** and `timezone_offset` is minutes east of UTC (plan D15). `websocket.token` is a placeholder — the real value is `JG_DEVICE_TOKEN` |
| `sse_chunk.txt` | Hermes → gateway | captured live from `POST http://127.0.0.1:8642/v1/chat/completions` on 2026-10-31 (T011, `docs/hermes-api-notes.md`). Role chunk, two content chunks, finish chunk with `usage`, `[DONE]`; ids and timestamps are the real ones from that response and carry no user content |

Deliberately absent, and therefore absent from every fixture: `activation` and `mqtt` in the OTA
manifest (plan C1 — no cloud account, no MQTT), and any `system`/`custom` message type (out of scope
for this feature).
