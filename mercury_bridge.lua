-- Mercury bridge v3. Load via mGBA Tools > Scripting > File > Load Script.
-- BATCH compares all expected bytes before writing in one Lua callback.
local PORT, MAX_LINE = 8888, 270000
local server, client = nil, nil
local input, output = "", ""
local outputLimit = 32768
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
-- BEGIN GENERATED WILD LAYOUTS (wild_encounter_layout.json)
local wildLayouts = {
  ["628607dcbeac3ab471310d5472c8fbd0df250745230207c488f66adbf1a43821"] = {
    generator = 165037976,
    stub_address = 145276928,
    base_stats_address = 158785468,
    base_stats_record_size = 28,
    stub_species_offset = 48,
    stub_level_offset = 26,
    original_generator = "f0b58bb00792012205000691",
    original_stub = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    hook_template = "08b4014b1847c04601c0a808",
    stub_template = "08bcf0b57446094d06262f68bc4203d00435013ef9d101e0054801218bb00792012205000691034c2047c04638c0a80801000000a547d6092d4ed609954ed609ff4ed6095b4fd609315ed609635ed609c046c046c046c046c046c046c046c046c046c046",
    species = {{1,251},{254,260},{262,411},{413,705},{707,1293},{1401,1488},{1491,1491},{1494,1525},{1527,1539},{1544,1553}},
    guards = {
      {134752764,"004a10479947d609"},
      {134753468,"004a10474f5ad609"},
      {165037976,"f0b58bb007920122050006910592002b04d05c4b01f040fd002305931c215a4b1a680b006b439b181b7c584a1400002b00d189e0fe2b00d386e0554f002211313800544b01f028fd061e00d07ce03800a3f728fa382800d076e04f4b01f01cfd032127f797fa0904090c00d16ce0388c39684a4b01f010fdfe2802d0002863d1"},
      {165038104,"fe36fef79ffc079907002800fff776ff6423020005985843029229002018019700962023069a3e4d01f0f8fc3d483e4d01f0f4fc002809d06422059b53438022e3184633597952420a435a71374801f0e5fc002808d06422059b53430822e3184033d9790a43da71314801f0d7fc002814d0059b5a42082364211a402d4bd218"},
      {165038232,"0023059c4c432c4ed55ab54203d01f4fe018c01985850233082bf5d1a8f7dcf96423059c5c43194be418002807d00127224d194e200001f0b1fc002818d12000a2f7ccfb0bb0f0bdfef73cfc079906002800fff713ff64230200059858430192201800962023069a2900a2f76dfb9de701f095fc09aa099039002000104b01f0"},
      {165038360,"8bfcd7e735da0308bc0100082c40020284420202e9fb0308c94e04088df7030801de03080f090000d1e60608280900000b090000b8700302ffff0000714404087d030408"},
      {165039376,"f0b50d25002489b004abed180700039105922c70a14200d183e0012936d1053400942b002a2208217868fff74dfa002834d100942b0009220d217868fff744fa00282bd100942b001f220d217868fff73bfa002822d100942b0012220a217868fff732fa002819d100942b00e1220c217868fff729fa002810d100942b008322"},
      {165039504,"0b217868fff720fa002807d10d2604abf618039b002b46d0022b49d92d787868ab00c0180493fff781fa059b0490db0740d4059b9b0720d58e4e00222d2130008d4b01f02dfa002817d13000a2f72eff332801d0162810d154363378052b0cd9049a043b934208dd844b01f019fa0221834b01f015fa002822d0039b002b21d1"},
      {165039632,"0e2604abf6183000fff74aff002819d0308801232a000499fff7b6fc180009b0f0bd0c247ce7774b01f0faf93070b5e7754bf9e70498754b01f0f2f90028b8d10023ebe77248734e01f0edf9002825d06a4b01f0e5f9704f0600704801f0e4f90a210400300026f759ff062363436c480a31c31809062a00090e040058880123"},
      {165039760,"fff782fc654801f0cff906264643a419a18800226348dcf7ddffbfe7039b002b61d11f2001f0bff900285cd0534b059301f0b6f9032126f731ff0904090c52d0594b5a4801f0acf9fff7d2fb059b060001f0a6f90a2126f721ff0904090c032935d8308801232a000499fff74dfc9120484b000101f094f90123002800d18de7"},
      {165039888,"0d2504abed1800232b709c4230d1039b002b64d0022b67d92c787868a6008019fff7c4f9039b0500002b5fd10e3304aa9b181800fff7b4fe002857d004abd889002322002900fff71ffc012366e70b061b0e052901d87088c4e7072b01d8b088c0e7f088bee77b68aa009b185888b9e700942b002a2208217868fff725f90028"},
      {165040016,"cad100942b0009220d217868fff71cf90028c1d100942b001f220d217868fff713f90028b8d100942b0012220a217868fff70af90028afd100942b00e1220c217868fff701f90028a6d100942b0083220b217868fff7f8f800289dd197e7094b01f01ef9287097e7074bf9e77b689e197088a5e7"},
      {165043704,"f7b505000220fff765fc0122264b1a70264b04002800019300f00efa870006006068c019fef74efa6368df190100012378883200fef7b4fc91201d4b000100f0fbf9002811d0019b280000f0f5f98600050060688019fef735fa63689b1901002a0058880023fef79bfc124b124800220b2100f0e1f9114e33880404240c104d"},
      {165043832,"a3420ed12878ff210130fcf77bfa28700c200c4b348000f0cff90b4b00f0ccf9f7bd00232b70f3e7"},
    },
  },
  ["b98d9701f4b567810c70221564c348f4482791c614c3f1bb282e96678b7a0896"] = {
    generator = 165055340,
    stub_address = 145276928,
    base_stats_address = 158785468,
    base_stats_record_size = 28,
    stub_species_offset = 48,
    stub_level_offset = 26,
    original_generator = "f0b58bb00792012205000691",
    original_stub = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    hook_template = "08b4014b1847c04601c0a808",
    stub_template = "08bcf0b57446094d06262f68bc4203d00435013ef9d101e0054801218bb00792012205000691034c2047c04638c0a80801000000798bd6092192d6098992d609f392d6094f93d60925a2d60957a2d609c046c046c046c046c046c046c046c046c046c046",
    species = {{1,251},{254,260},{262,411},{413,705},{707,1293},{1401,1488},{1491,1491},{1494,1525},{1527,1539},{1544,1553}},
    guards = {
      {134752764,"004a10476d8bd609"},
      {134753468,"004a1047439ed609"},
      {165055340,"f0b58bb007920122050006910592002b04d05c4b01f050fd002305931c215a4b1a680b006b439b181b7c584a1400002b00d189e0fe2b00d386e0554f002211313800544b01f038fd061e00d07ce03800a1f780fa382800d076e04f4b01f02cfd032123f7adf80904090c00d16ce0388c39684a4b01f020fdfe2802d0002863d1"},
      {165055468,"fe36fef773fc079907002800fff760ff6423020005985843029229002018019700962023069a3e4d01f008fd3d483e4d01f004fd002809d06422059b53438022e3184633597952420a435a71374801f0f5fc002808d06422059b53430822e3184033d9790a43da71314801f0e7fc002814d0059b5a42082364211a402d4bd218"},
      {165055596,"0023059c4c432c4ed55ab54203d01f4fe018c01985850233082bf5d1a6f734fa6423059c5c43194be418002807d00127224d194e200001f0c1fc002818d12000a0f724fc0bb0f0bdfef710fc079906002800fff7fdfe64230200059858430192201800962023069a2900a0f7c5fb9de701f0a5fc09aa099039002000104b01f0"},
      {165055724,"9bfcd7e735da0308bc0100082c40020284420202e9fb0308c94e04088df7030801de03080f090000d1e60608280900000b090000b8700302ffff0000714404087d030408"},
      {165056772,"f0b50d25002489b004abed180700039105922c70a14200d183e0012936d1053400942b002a2208217868fff727fa002834d100942b0009220d217868fff71efa00282bd100942b001f220d217868fff715fa002822d100942b0012220a217868fff70cfa002819d100942b00e1220c217868fff703fa002810d100942b008322"},
      {165056900,"0b217868fff7faf9002807d10d2604abf618039b002b46d0022b49d92d787868ab00c0180493fff75bfa059b0490db0740d4059b9b0720d58e4e00222d2130008d4b01f02dfa002817d13000a0f776ff332801d0162810d154363378052b0cd9049a043b934208dd844b01f019fa0221834b01f015fa002822d0039b002b21d1"},
      {165057028,"0e2604abf6183000fff74aff002819d0308801232a000499fff7a6fc180009b0f0bd0c247ce7774b01f0faf93070b5e7754bf9e70498754b01f0f2f90028b8d10023ebe77248734e01f0edf9002825d06a4b01f0e5f9704f0600704801f0e4f90a210400300022f75ffd062363436c480a31c31809062a00090e040058880123"},
      {165057156,"fff772fc654801f0cff906264643a419a18800226348dbf7f3febfe7039b002b61d11f2001f0bff900285cd0534b059301f0b6f9032122f737fd0904090c52d0594b5a4801f0acf9fff7acfb059b060001f0a6f90a2122f727fd0904090c032935d8308801232a000499fff73dfc9120484b000101f094f90123002800d18de7"},
      {165057284,"0d2504abed1800232b709c4230d1039b002b64d0022b67d92c787868a6008019fff79ef9039b0500002b5fd10e3304aa9b181800fff7b4fe002857d004abd889002322002900fff70ffc012366e70b061b0e052901d87088c4e7072b01d8b088c0e7f088bee77b68aa009b185888b9e700942b002a2208217868fff7fff80028"},
      {165057412,"cad100942b0009220d217868fff7f6f80028c1d100942b001f220d217868fff7edf80028b8d100942b0012220a217868fff7e4f80028afd100942b00e1220c217868fff7dbf80028a6d100942b0083220b217868fff7d2f800289dd197e7094b01f01ef9287097e7074bf9e77b689e197088a5e7"},
      {165061100,"f7b505000220fff765fc0122264b1a70264b04002800019300f00efa870006006068c019fef728fa6368df190100012378883200fef7a4fc91201d4b000100f0fbf9002811d0019b280000f0f5f98600050060688019fef70ffa63689b1901002a0058880023fef78bfc124b124800220b2100f0e1f9114e33880404240c104d"},
      {165061228,"a3420ed12878ff210130fcf73ffa28700c200c4b348000f0cff90b4b00f0ccf9f7bd00232b70f3e7"},
    },
  },
  ["9801866280284a8ac12650666b8526ff3d07aa455f8dc623c2c56c0537313ec4"] = {
    generator = 165067384,
    stub_address = 145276928,
    base_stats_address = 158785468,
    base_stats_record_size = 28,
    stub_species_offset = 48,
    stub_level_offset = 26,
    original_generator = "f0b58bb00792012205000691",
    original_stub = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    hook_template = "08b4014b1847c04601c0a808",
    stub_template = "08bcf0b57446094d06262f68bc4203d00435013ef9d101e0054801218bb00792012205000691034c2047c04638c0a8080100000085bad6092dc1d60995c1d609ffc1d6095bc2d60931d1d60963d1d609c046c046c046c046c046c046c046c046c046c046",
    species = {{1,251},{254,260},{262,411},{413,705},{707,1293},{1401,1488},{1491,1491},{1494,1525},{1527,1539},{1544,1553}},
    guards = {
      {134752764,"004a104779bad609"},
      {134753468,"004a10474fcdd609"},
      {165067384,"f0b58bb007920122050006910592002b04d05c4b01f050fd002305931c215a4b1a680b006b439b181b7c584a1400002b00d189e0fe2b00d386e0554f002211313800544b01f038fd061e00d07ce038009ef774ff382800d076e04f4b01f02cfd032120f727f90904090c00d16ce0388c39684a4b01f020fdfe2802d0002863d1"},
      {165067512,"fe36fef76dfc079907002800fff760ff6423020005985843029229002018019700962023069a3e4d01f008fd3d483e4d01f004fd002809d06422059b53438022e3184633597952420a435a71374801f0f5fc002808d06422059b53430822e3184033d9790a43da71314801f0e7fc002814d0059b5a42082364211a402d4bd218"},
      {165067640,"0023059c4c432c4ed55ab54203d01f4fe018c01985850233082bf5d1a3f728ff6423059c5c43194be418002807d00127224d194e200001f0c1fc002818d120009ef718f90bb0f0bdfef70afc079906002800fff7fdfe64230200059858430192201800962023069a29009ef7b9f89de701f0a5fc09aa099039002000104b01f0"},
      {165067768,"9bfcd7e735da0308bc0100082c40020284420202e9fb0308c94e04088df7030801de03080f090000d1e60608280900000b090000b8700302ffff0000714404087d030408"},
      {165068816,"f0b50d25002489b004abed180700039105922c70a14200d183e0012936d1053400942b002a2208217868fff727fa002834d100942b0009220d217868fff71efa00282bd100942b001f220d217868fff715fa002822d100942b0012220a217868fff70cfa002819d100942b00e1220c217868fff703fa002810d100942b008322"},
      {165068944,"0b217868fff7faf9002807d10d2604abf618039b002b46d0022b49d92d787868ab00c0180493fff75bfa059b0490db0740d4059b9b0720d58e4e00222d2130008d4b01f02dfa002817d130009ef76afc332801d0162810d154363378052b0cd9049a043b934208dd844b01f019fa0221834b01f015fa002822d0039b002b21d1"},
      {165069072,"0e2604abf6183000fff74aff002819d0308801232a000499fff7a6fc180009b0f0bd0c247ce7774b01f0faf93070b5e7754bf9e70498754b01f0f2f90028b8d10023ebe77248734e01f0edf9002825d06a4b01f0e5f9704f0600704801f0e4f90a21040030001ff7d9fd062363436c480a31c31809062a00090e040058880123"},
      {165069200,"fff772fc654801f0cff906264643a419a18800226348daf769fdbfe7039b002b61d11f2001f0bff900285cd0534b059301f0b6f903211ff7b1fd0904090c52d0594b5a4801f0acf9fff7acfb059b060001f0a6f90a211ff7a1fd0904090c032935d8308801232a000499fff73dfc9120484b000101f094f90123002800d18de7"},
      {165069328,"0d2504abed1800232b709c4230d1039b002b64d0022b67d92c787868a6008019fff79ef9039b0500002b5fd10e3304aa9b181800fff7b4fe002857d004abd889002322002900fff70ffc012366e70b061b0e052901d87088c4e7072b01d8b088c0e7f088bee77b68aa009b185888b9e700942b002a2208217868fff7fff80028"},
      {165069456,"cad100942b0009220d217868fff7f6f80028c1d100942b001f220d217868fff7edf80028b8d100942b0012220a217868fff7e4f80028afd100942b00e1220c217868fff7dbf80028a6d100942b0083220b217868fff7d2f800289dd197e7094b01f01ef9287097e7074bf9e77b689e197088a5e7"},
      {165073144,"f7b505000220fff765fc0122264b1a70264b04002800019300f00efa870006006068c019fef728fa6368df190100012378883200fef7a4fc91201d4b000100f0fbf9002811d0019b280000f0f5f98600050060688019fef70ffa63689b1901002a0058880023fef78bfc124b124800220b2100f0e1f9114e33880404240c104d"},
      {165073272,"a3420ed12878ff210130fcf739fa28700c200c4b348000f0cff90b4b00f0ccf9f7bd00232b70f3e7"},
    },
  },
}
-- END GENERATED WILD LAYOUTS

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
-- Only these two audited spans can be written through the cartridge domain.
-- No protocol field accepts an address or replacement bytes.
local wild, wildReason = nil, "idle"
local WILD_LEASE = 3600
local function wildCapable()
    return emu.memory and emu.memory.cart0 and emu.memory.cart0.write8 and emu.readRegister
end
local function rawWrite(address, bytes)
    for i=1,#bytes do emu.memory.cart0:write8(address-0x08000000+i-1,bytes:byte(i)) end
end
local function wildPatches(layout, species, level)
    local stub = unhex(layout.stub_template)
    local at = layout.stub_species_offset
    local le = string.char(species % 256, math.floor(species/256) % 256,0,0)
    stub = stub:sub(1,at)..le..stub:sub(at+5)
    at = layout.stub_level_offset
    stub = stub:sub(1,at)..string.char(level)..stub:sub(at+2)
    return {{addr=layout.generator,before=unhex(layout.original_generator),after=unhex(layout.hook_template)},
            {addr=layout.stub_address,before=unhex(layout.original_stub),after=stub}}
end
local function matching(patches, field)
    for _,p in ipairs(patches) do
        if emu:readRange(p.addr,#p[field]) ~= p[field] then return false end
    end
    return true
end
local function wildSafe(layout)
    local pc=emu:readRegister("pc")
    if type(pc)~="number" then return false end
    -- Include the Thumb prefetch margin; no half-executed trampoline is removed.
    return not ((pc>=layout.generator and pc<layout.generator+16) or
                (pc>=layout.stub_address and pc<layout.stub_address+104))
end
-- Restore progress is an exact byte ledger. Only a write attempted inside this
-- callback may change its expected value; a later third-party before/after byte
-- is still foreign. Never infer ownership merely from membership in that pair.
local function restoreMatches(current)
    for index,p in ipairs(current.patches) do
        if emu:readRange(p.addr,#p.before)~=current.restoreExpected[index] then return false end
    end
    return true
end
local function wildRestoreAttempt(current)
    if hex(emu:checksum()) ~= current.crc then
        wild=nil;wildReason="rom_changed";return false
    end
    if not wildSafe(current.layout) then wildReason="pending_stop";return false end
    if not current.restoreExpected then
        if not matching(current.patches,"after") then wildReason="modified";return false end
        current.restoreExpected={current.patches[1].after,current.patches[2].after}
        current.restoreIndex,current.restoreOffset=1,1
    end
    if current.restoreAttempt then
        -- A readback may itself raise after the write. Resolve only that one
        -- attempted byte; all other bytes must still match the exact ledger.
        local attempt=current.restoreAttempt
        local observed=emu:readRange(attempt.addr,1):byte(1)
        if observed~=attempt.old and observed~=attempt.target then wildReason="modified";return false end
        local expected=current.restoreExpected[attempt.index]
        current.restoreExpected[attempt.index]=expected:sub(1,attempt.offset-1)..string.char(observed)..expected:sub(attempt.offset+1)
        if observed==attempt.target and attempt.advance~=false then current.restoreOffset=attempt.offset+1 end
        current.restoreAttempt=nil
    end
    if not restoreMatches(current) then wildReason="modified";return false end
    while current.restoreIndex<=#current.patches do
        local index=current.restoreIndex
        local p=current.patches[index]
        while current.restoreOffset<=#p.before do
            local offset=current.restoreOffset
            local expected=current.restoreExpected[index]
            local old,target=expected:byte(offset),p.before:byte(offset)
            -- Capture even a write which changes its byte and then raises.
            current.restoreAttempt={index=index,offset=offset,addr=p.addr+offset-1,old=old,target=target}
            local ok=pcall(function()
                emu.memory.cart0:write8(p.addr-0x08000000+offset-1,target)
            end)
            local observed=emu:readRange(p.addr+offset-1,1):byte(1)
            if observed==target then
                current.restoreExpected[index]=expected:sub(1,offset-1)..string.char(target)..expected:sub(offset+1)
                current.restoreOffset=offset+1
            elseif observed~=old then
                wildReason="modified";return false
            end
            current.restoreAttempt=nil
            if not ok or observed~=target then wildReason="restore_readback";return false end
        end
        -- The hook must be fully original before the stub can be touched.
        if emu:readRange(p.addr,#p.before)~=p.before then wildReason="modified";return false end
        current.restoreIndex,current.restoreOffset=index+1,1
    end
    if not matching(current.patches,"before") then wildReason="modified";return false end
    wild=nil;wildReason=current.pending;return true
end
local function wildRollbackHook(current)
    -- A failed unhook may leave a mixed entry. While the stub is still wholly
    -- installed, repair only our exact known hook bytes back to the installed
    -- hook before returning control to the CPU. The persistent failing stop byte
    -- is normally already "after" and is consequently never written here.
    if current.restoreIndex~=1 or current.restoreAttempt then return false end
    local hook,stub=current.patches[1],current.patches[2]
    if emu:readRange(stub.addr,#stub.after)~=stub.after or not restoreMatches(current) then return false end
    current.restoreOffset=1
    for offset=1,#hook.after do
        local expected=current.restoreExpected[1]
        local old,target=expected:byte(offset),hook.after:byte(offset)
        if old~=target then
            current.restoreAttempt={index=1,offset=offset,addr=hook.addr+offset-1,old=old,target=target,advance=false}
            local ok=pcall(function() emu.memory.cart0:write8(hook.addr-0x08000000+offset-1,target) end)
            local observed=emu:readRange(hook.addr+offset-1,1):byte(1)
            if observed==target then
                current.restoreExpected[1]=expected:sub(1,offset-1)..string.char(target)..expected:sub(offset+1)
            elseif observed~=old then wildReason="modified";return false end
            current.restoreAttempt=nil
            if not ok and observed~=target then return false end
            if observed~=target then return false end
        end
    end
    return matching(current.patches,"after")
end
local function wildRestore(reason)
    if not wild then return true end
    local current=wild
    current.pending=current.pending or reason
    -- Resolve transient failures before yielding the callback to the CPU. A
    -- persistent domain failure still cannot guarantee safe continued execution;
    -- retain ERROR and ownership so callers can require a cartridge reload.
    for attempt=1,3 do
        local ok,restored=pcall(wildRestoreAttempt,current)
        if ok and restored then return true end
        if not ok then wildReason="restore_readback" end
        if not wild or wildReason~="restore_readback" then return false end
    end
    if current.restoreIndex==1 then
        local ok,repaired=pcall(wildRollbackHook,current)
        if ok and repaired then wildReason="pending_stop" end
    end
    return false
end
local function wildGuardCheck(current)
    if hex(emu:checksum()) ~= current.crc or not matching(current.patches,"after") then return false end
    for _,guard in ipairs(current.layout.guards) do
        local before = unhex(guard[2])
        local raw = emu:readRange(guard[1],#before)
        for _,p in ipairs(current.patches) do
            local first,last=math.max(guard[1],p.addr),math.min(guard[1]+#before,p.addr+#p.before)
            if first<last then
                raw=raw:sub(1,first-guard[1])..p.before:sub(first-p.addr+1,last-p.addr)..raw:sub(last-guard[1]+1)
            end
        end
        if raw ~= before then return false end
    end
    return true
end
local function wildTick()
    if not wild then return end
    local ok=pcall(function()
        if wild.pending then wildRestore(wild.pending);return end
        wild.frames=wild.frames-1
        if wild.frames<=0 then wildRestore("lease_expired")
        elseif wild.frames%60==0 and not wildGuardCheck(wild) then wildRestore("guard_changed") end
    end)
    if not ok and wild then
        wild.pending=wild.pending or "guard_changed";wildReason="restore_readback"
    end
end
local function wildState()
    if not wild then return "INACTIVE "..wildReason end
    if wildReason=="modified" or wildReason=="restore_readback" or wild.pending then
        return "ERROR "..wild.nonce.." "..wildReason
    end
    return table.concat({"ACTIVE",wild.nonce,wild.crc,wild.species,wild.level,wild.frames}," ")
end
local function wildCommand(cmd, rest)
    if not wildCapable() then return "ERR unsupported" end
    if cmd=="WILDSTATE" then
        if wild and not wild.pending and not wildGuardCheck(wild) then wildRestore("guard_changed") end
        return wildState()
    end
    if cmd=="WILDON" then
        if wild then return "ERR active" end
        local crc,sha,species,level,nonce=rest:match("^(%x+) (%x+) (%d+) (%d+) (%x+)$")
        if not crc or #crc~=8 or #sha~=64 or #nonce~=32 then return "ERR format" end
        species,level=tonumber(species),tonumber(level)
        local layout=wildLayouts[sha:lower()]
        if not layout or level<1 or level>100 then return "ERR range" end
        if hex(emu:checksum())~=crc:lower() then return "ERR rom" end
        local valid=false
        for _,range in ipairs(layout.species) do if species>=range[1] and species<=range[2] then valid=true end end
        if not valid then return "ERR species" end
        local stats=emu:readRange(layout.base_stats_address+species*layout.base_stats_record_size,6)
        if #stats~=6 or stats:find(string.char(0),1,true) then return "ERR species" end
        for _,g in ipairs(layout.guards) do
            local before=unhex(g[2]);if emu:readRange(g[1],#before)~=before then return "ERR stale" end
        end
        if math.floor(emu:readRange(0x03003529,1):byte(1)/2)%2==1 then return "ERR battle" end
        if not wildSafe(layout) then return "ERR busy" end
        local patches=wildPatches(layout,species,level)
        if not matching(patches,"before") then return "ERR stale" end
        local state={layout=layout,patches=patches,crc=crc:lower(),nonce=nonce:lower(),species=species,level=level,frames=WILD_LEASE}
        -- Stub first, then hook, within one callback (the CPU cannot run here).
        wild=state;wildReason="applying"
        local ok=pcall(function()
            rawWrite(patches[2].addr,patches[2].after)
            if emu:readRange(patches[2].addr,#patches[2].after)~=patches[2].after then error("stub readback") end
            rawWrite(patches[1].addr,patches[1].after)
            if not matching(patches,"after") then error("hook readback") end
        end)
        if not ok then
            -- Applying has not yielded the CPU. Record only bytes from our
            -- attempted writes, then use the same exact-progress restore path.
            local safe,expected=true,{}
            for index,p in ipairs(patches) do
                local raw=emu:readRange(p.addr,#p.after)
                if #raw~=#p.after then safe=false end
                for i=1,#raw do
                    if raw:byte(i)~=p.after:byte(i) and raw:byte(i)~=p.before:byte(i) then safe=false end
                end
                expected[index]=raw
            end
            if safe then
                state.restoreExpected=expected;state.restoreIndex=1;state.restoreOffset=1
                wildRestore("apply_failed")
            else
                state.pending="apply_failed";wildReason="modified"
            end
            return "ERR readback"
        end
        wild=state;wildReason="active"
        return "OK"
    end
    if cmd=="WILDOFF" or cmd=="WILDKEEP" then
        if not rest or not rest:match("^%x+$") or #rest~=32 then return "ERR format" end
        if not wild then return "OK" end
        if rest:lower()~=wild.nonce then return "ERR owner" end
        if cmd=="WILDOFF" then return wildRestore("stopped") and "OK" or "ERR restore" end
        if wild.pending then return "ERR stopping" end
        if not wildGuardCheck(wild) then wildRestore("guard_changed");return "ERR guard" end
        wild.frames=WILD_LEASE;return "OK"
    end
    return "ERR unsupported"
end

local function handle(line)
    if #line > MAX_LINE then return "ERR limit" end
    if line == "PING" then return "PONG" end
    if line == "CAPS" then return "MERCURY/3 BATCH ROMCRC CRCBATCH BATCH8192 BATCHVERIFY BOXBATCH ROMREAD"..(wildCapable() and " WILDNATIVE1" or "") end
    if line == "ROMCRC" then return hex(emu:checksum()) end
    if line == "WILDSTATE" then return wildCommand("WILDSTATE", "") end
    local cmd, rest = line:match("^(%S+)%s+(.*)$")
    if cmd and cmd:sub(1,4)=="WILD" then return wildCommand(cmd,rest) end
    if cmd == "BATCHCRC" or cmd == "BATCHVERIFYCRC" or cmd == "BOXBATCHCRC" then
        if cmd ~= "BOXBATCHCRC" and #line > 40000 then return "ERR limit" end
        local expected, patches=rest:match("^(%x+)%s+(.+)$")
        if not expected or #expected ~= 8 then return "ERR format" end
        if hex(emu:checksum()) ~= expected:lower() then return "ERR rom" end
        return batch(patches, cmd ~= "BATCHCRC", cmd == "BOXBATCHCRC")
    end
    if cmd == "READ" or cmd == "READROM" then
        local addr, count = rest:match("^(%x+)%s+(%x+)$")
        if not addr then return "ERR format" end
        addr,count = tonumber(addr,16),tonumber(count,16)
        if cmd == "READROM" then
            if count < 1 or count > 65536 or addr < 0x08000000 or addr + count > 0x0A000000 then return "ERR range" end
        elseif not readable(addr,count) then return "ERR range" end
        return hex(emu:readRange(addr,count))
    end
    return "ERR unsupported"
end
-- Explicitly enabled only by the offline test harness.
if MERCURY_BRIDGE_TEST then
    MERCURY_WILD_TEST={tick=wildTick,disconnect=function() wildRestore("disconnected") end}
    return handle
end
local function disconnect()
    pcall(wildRestore,"disconnected")
    if client then client:close() end
    client=nil
    input,output="",""
    outputLimit=32768
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
    if #output == 0 then outputLimit=32768 end
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
            response=(ok and response or "ERR internal").."\n"
            local responseLimit=line:match("^READROM%s") and 131073 or 32768
            if #response > responseLimit then disconnect(); return end
            outputLimit=math.max(outputLimit,responseLimit)
            output=output..response
            if #output>outputLimit then disconnect(); return end
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
    outputLimit=32768
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
callbacks:add("frame",function() wildTick();flush() end)
callbacks:add("reset",function() wildRestore("reset") end)
if not server then console:log("[mercury v3] no port available") end
