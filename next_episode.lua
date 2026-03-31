-- next_episode.lua  —  Great Sage
-- Place in the same folder as great_sage_gui.py
-- Ctrl+Right plays the next episode immediately (no popup, no percentage trigger).

local mp      = require "mp"
local options = require "mp.options"

local opts = {
    has_next = "yes",
}
options.read_options(opts, "next_episode")

-- ── Reset state when a new file loads ────────────────────────────────────────
mp.register_event("file-loaded", function()
    -- stateless now, nothing to reset
end)

-- ── Ctrl+Right: play next immediately ────────────────────────────────────────
local function play_next()
    if opts.has_next ~= "yes" then return end
    mp.set_property("user-data/gs-next", "yes")
end

-- ── has_next message from Python ─────────────────────────────────────────────
mp.register_script_message("next-episode-has-next", function(val)
    opts.has_next = val
end)

mp.add_key_binding("Ctrl+RIGHT", "gs-next-play", play_next, {repeatable=false})
