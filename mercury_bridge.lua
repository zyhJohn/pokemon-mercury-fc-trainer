-- Mercury bridge v3. Load via mGBA Tools > Scripting > File > Load Script.
-- BATCH compares all expected bytes before writing in one Lua callback.
local PORT, MAX_LINE = 8888, 270000
local server, client = nil, nil
local input, output = "", ""
local function hex(bytes)
    return (bytes:gsub(".", function(c) return string.format("%02x", string.byte(c)) end))
end
local function unhex(value)
    if #value % 2 ~= 0 or value:find("[^%x]") then return nil end
    return (value:gsub("%x%x", function(pair) return string.char(tonumber(pair,16)) end))
end
local function readable(address, size)
    return size >= 1 and size <= 4096 and (
        (address >= 0x02000000 and address + size <= 0x02040000) or
        (address >= 0x03000000 and address + size <= 0x03008000) or
        (address >= 0x08000000 and address + size <= 0x0A000000))
end
local function batch(rest, verify, largeBoxes)
    if rest == "" or rest:sub(-1) == ";" or rest:find(";;",1,true) then return "ERR format" end
    local patches, total = {}, 0
    for part in rest:gmatch("[^;]+") do
        local addr, before, after = part:match("^(%x+):(%x+):(%x+)$")
        if not addr then return "ERR format" end
        addr, before, after = tonumber(addr,16), unhex(before), unhex(after)
        if not before or not after or #before == 0 or #before ~= #after then return "ERR length" end
        local compareOnly = before == after
        if largeBoxes and not compareOnly and #before ~= 1740 then return "ERR length" end
        if not readable(addr,#before) or
            (not compareOnly and not (addr >= 0x02000000 and addr + #before <= 0x02040000)) then
            return "ERR range"
        end
        for _, p in ipairs(patches) do
            if addr < p.addr + #p.before and p.addr < addr + #before then return "ERR overlap" end
        end
        patches[#patches+1] = {addr=addr,before=before,after=after}
        total = total + #before
        if #patches > (largeBoxes and 96 or 64) or total > (largeBoxes and 65536 or 8192) then return "ERR limit" end
    end
    for _,p in ipairs(patches) do
        if emu:readRange(p.addr,#p.before) ~= p.before then return "ERR stale" end
    end
    for _,p in ipairs(patches) do
        if p.before ~= p.after then
            for i=1,#p.after do emu:write8(p.addr+i-1,string.byte(p.after,i)) end
        end
    end
    if verify then
        for _,p in ipairs(patches) do
            if p.before ~= p.after and emu:readRange(p.addr,#p.after) ~= p.after then return "ERR readback" end
        end
    end
    return "OK"
end
local function handle(line)
    if #line > MAX_LINE then return "ERR limit" end
    if line == "PING" then return "PONG" end
    if line == "CAPS" then return "MERCURY/3 BATCH ROMCRC CRCBATCH BATCH8192 BATCHVERIFY BOXBATCH" end
    if line == "ROMCRC" then return hex(emu:checksum()) end
    local cmd, rest = line:match("^(%S+)%s+(.*)$")
    if cmd == "BATCHCRC" or cmd == "BATCHVERIFYCRC" or cmd == "BOXBATCHCRC" then
        if cmd ~= "BOXBATCHCRC" and #line > 40000 then return "ERR limit" end
        local expected, patches=rest:match("^(%x+)%s+(.+)$")
        if not expected or #expected ~= 8 then return "ERR format" end
        if hex(emu:checksum()) ~= expected:lower() then return "ERR rom" end
        return batch(patches, cmd ~= "BATCHCRC", cmd == "BOXBATCHCRC")
    end
    if cmd == "READ" then
        local addr, count = rest:match("^(%x+)%s+(%x+)$")
        if not addr then return "ERR format" end
        addr,count = tonumber(addr,16),tonumber(count,16)
        if not readable(addr,count) then return "ERR range" end
        return hex(emu:readRange(addr,count))
    end
    return "ERR unsupported"
end
-- Explicitly enabled only by the offline test harness.
if MERCURY_BRIDGE_TEST then return handle end
local function disconnect()
    if client then client:close() end
    client=nil
    input,output="",""
end
local function flush()
    while client and #output > 0 do
        local sent,err=client:send(output)
        if not sent or sent <= 0 then
            if err ~= socket.ERRORS.AGAIN then disconnect() end
            return
        end
        output=output:sub(sent+1)
    end
end
local function received()
    while client do
        local data,err=client:receive(4096)
        if not data or #data==0 then
            if err ~= socket.ERRORS.AGAIN then disconnect() end
            return
        end
        input=input..data
        if #input > MAX_LINE or (#input > 40000 and input:sub(1,12) ~= "BOXBATCHCRC ") then disconnect(); return end
        while true do
            local nl=input:find("\n",1,true)
            if not nl then break end
            local line=input:sub(1,nl-1)
            input=input:sub(nl+1)
            local ok,response=pcall(handle,line)
            output=output..(ok and response or "ERR internal").."\n"
            if #output>32768 then disconnect(); return end
            flush()
        end
    end
end
local function accept()
    local incoming=server:accept()
    if not incoming then return end
    if client then incoming:close(); return end
    client=incoming
    input,output="",""
    client:add("received",function() if client == incoming then received() end end)
    client:add("error",function() if client == incoming then disconnect() end end)
end
for port=PORT,PORT+7 do
    local candidate,err=socket.bind("127.0.0.1",port)
    if candidate then
        local _,listenError=candidate:listen()
        if not listenError then
            server=candidate
            server:add("received",accept)
            console:log("[mercury v3] listening on 127.0.0.1:"..port)
            break
        end
        candidate:close()
    elseif err ~= socket.ERRORS.ADDRESS_IN_USE then
        console:log("[mercury v3] bind error: "..tostring(err))
        break
    end
end
callbacks:add("frame",flush)
if not server then console:log("[mercury v3] no port available") end
