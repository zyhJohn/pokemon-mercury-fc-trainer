-- mercury_bridge.lua
-- 宝可梦水银FC 修改器 内存桥接脚本
-- 在 mGBA 内部监听 TCP 端口，向外部修改器提供内存读写服务，
-- 替代 GDB 调试桩（无需 -g 参数、无需 cmd 启动 mGBA）。
--
-- 用法：mGBA 菜单 Tools -> Scripting -> File -> Load Script 选择本文件。
-- 加载后脚本常驻，直到关闭 mGBA。
--
-- 协议（纯文本，每条命令以换行结尾，响应以换行结尾）：
--   READ  <hex addr> <hex len>       -> 返回 hex 字节串
--   READ8 <hex addr>                 -> 返回十进制（0-255）
--   READ16<hex addr>                 -> 返回十进制
--   READ32<hex addr>                 -> 返回十进制
--   WRITE8 <hex addr> <dec val>      -> OK / ERR
--   WRITE16<hex addr> <dec val>      -> OK / ERR
--   WRITE32<hex addr> <dec val>      -> OK / ERR
--   WRITE <hex addr> <hex bytes>     -> OK / ERR
--   PING                             -> PONG
--
-- 端口默认 8888，被占用时自动 +1 递增，最终端口打印到 console。

local PORT = 8888
local server = nil
local client = nil
local buf = ""

local function log(msg)
    console:log("[mercury] " .. tostring(msg))
end

local function hex_to_num(s)
    return tonumber(s, 16)
end

local function num_to_hex_str(bytes)
    -- bytes 是 string，转成小写十六进制
    return (bytes:gsub(".", function(c)
        return string.format("%02x", string.byte(c))
    end))
end

-- 处理一行命令
local function handle_line(line)
    line = line:match("^%s*(.-)%s*$")  -- trim
    if line == "" then return nil end

    local cmd, rest = line:match("^(%S+)%s*(.*)$")
    if not cmd then return nil end

    local resp = nil

    if cmd == "PING" then
        resp = "PONG"

    elseif cmd == "READ" then
        local addr_s, len_s = rest:match("^(%x+)%s+(%x+)$")
        if addr_s and len_s then
            local addr = hex_to_num(addr_s)
            local len = hex_to_num(len_s)
            local bytes = emu:readRange(addr, len)
            resp = num_to_hex_str(bytes)
        else
            resp = "ERR bad args"
        end

    elseif cmd == "READ8" then
        local addr = hex_to_num(rest)
        if addr then
            resp = tostring(emu:read8(addr))
        else resp = "ERR bad addr" end

    elseif cmd == "READ16" then
        local addr = hex_to_num(rest)
        if addr then
            resp = tostring(emu:read16(addr))
        else resp = "ERR bad addr" end

    elseif cmd == "READ32" then
        local addr = hex_to_num(rest)
        if addr then
            resp = tostring(emu:read32(addr))
        else resp = "ERR bad addr" end

    elseif cmd == "WRITE8" then
        local addr_s, val_s = rest:match("^(%x+)%s+(%d+)$")
        if addr_s and val_s then
            emu:write8(hex_to_num(addr_s), tonumber(val_s))
            resp = "OK"
        else resp = "ERR bad args" end

    elseif cmd == "WRITE16" then
        local addr_s, val_s = rest:match("^(%x+)%s+(%d+)$")
        if addr_s and val_s then
            emu:write16(hex_to_num(addr_s), tonumber(val_s))
            resp = "OK"
        else resp = "ERR bad args" end

    elseif cmd == "WRITE32" then
        local addr_s, val_s = rest:match("^(%x+)%s+(%d+)$")
        if addr_s and val_s then
            emu:write32(hex_to_num(addr_s), tonumber(val_s))
            resp = "OK"
        else resp = "ERR bad args" end

    elseif cmd == "WRITE" then
        local addr_s, hex_s = rest:match("^(%x+)%s+(%x+)$")
        if addr_s and hex_s then
            local addr = hex_to_num(addr_s)
            local bytes = hex_s:gsub("%x%x", function(pair)
                return string.char(tonumber(pair, 16))
            end)
            local ok = true
            for i = 1, #bytes do
                emu:write8(addr + i - 1, string.byte(bytes, i))
            end
            resp = "OK"
        else resp = "ERR bad args" end

    else
        resp = "ERR unknown cmd"
    end

    return resp
end

-- 客户端断开处理（需在 on_client_received 之前定义，否则被当作全局变量导致 nil 报错）
local function on_client_error()
    if not client then return end
    local c = client
    client = nil
    buf = ""
    log("client disconnected")
    c:close()
end

-- 客户端数据回调
local function on_client_received()
    if not client then return end
    while true do
        local chunk, err = client:receive(4096)
        if chunk then
            buf = buf .. chunk
            -- 按行切分处理
            while true do
                local nl = buf:find("\n", 1, true)
                if not nl then break end
                local line = buf:sub(1, nl - 1)
                buf = buf:sub(nl + 1)
                local resp = handle_line(line)
                if resp then
                    client:send(resp .. "\n")
                end
            end
        else
            if err and err ~= socket.ERRORS.AGAIN then
                on_client_error()
            end
            return
        end
    end
end

local function on_accept()
    if client then
        -- 已有客户端，拒绝新连接（单连接，简化处理）
        local c = server:accept()
        if c then c:close() end
        return
    end
    client = server:accept()
    if client then
        log("client connected")
        client:add("received", on_client_received)
        client:add("error", on_client_error)
    end
end

-- 启动服务
local function start()
    local port = PORT
    local bound = false
    while not bound and port < PORT + 100 do
        server, err = socket.bind(nil, port)
        if err then
            if err == socket.ERRORS.ADDRESS_IN_USE then
                port = port + 1
            else
                log("bind error: " .. tostring(err))
                return
            end
        else
            local ok
            ok, err = server:listen()
            if err then
                server:close()
                server = nil
                log("listen error: " .. tostring(err))
                return
            end
            bound = true
        end
    end
    if not bound then
        log("failed to bind any port")
        return
    end
    server:add("received", on_accept)
    log("listening on port " .. port)
end

-- 等待游戏加载后启动（emu 对象在游戏加载后可用）
local started = false
callbacks:add("frame", function()
    if not started then
        started = true
        start()
    end
end)

log("script loaded")
