--[[
  rambass_build_song.lua — build a Reaper project from a rambass build script.

  Usage
  -----
  1. Generate a build script:      rambass reaper build <song>
  2. In Reaper: File > New Project
  3. Actions > Show action list > ReaScript: Load... > pick this file, then Run
  4. Choose the .rbs file from reaper/build/

  It creates the tracks, imports the count-in / click / backing / drum MIDI /
  reference stems, writes the tempo map and drops a marker and a region on every
  section.

  Two separate count-in tracks, and they are not interchangeable:
    STICKS  the drumstick count-in only. A musical part; may go to the PA.
    CLICK   the click for the song itself, for rehearsal and overdubs.
            Muted on build, and it must stay out of front of house.
  Neither is ever mixed into the backing track.

  Nothing here is destructive: it only adds to the current project. Run it on a
  fresh project, or on a copy.

  Tested against the Reaper 7 API.
--]]

local VERSION = 1

----------------------------------------------------------------------------
-- helpers
----------------------------------------------------------------------------

local function log(fmt, ...)
  reaper.ShowConsoleMsg(string.format(fmt .. "\n", ...))
end

local function split_tabs(line)
  local fields = {}
  for field in string.gmatch(line .. "\t", "([^\t]*)\t") do
    fields[#fields + 1] = field
  end
  return fields
end

local function trim(text)
  return (text:gsub("^%s*(.-)%s*$", "%1"))
end

local function tonum(value, fallback)
  return tonumber(value) or fallback
end

-- Reaper wants colours as native ints; on every platform ColorToNative +
-- 0x1000000 (the "colour is set" flag) is the documented way.
local function parse_color(text)
  if not text or text == "" then return nil end
  local r, g, b = text:match("(%d+),(%d+),(%d+)")
  if not r then return nil end
  return reaper.ColorToNative(tonumber(r), tonumber(g), tonumber(b)) | 0x1000000
end

----------------------------------------------------------------------------
-- track handling
----------------------------------------------------------------------------

local tracks_by_name = {}

local function add_track(name, volume_db, pan, color)
  local index = reaper.CountTracks(0)
  reaper.InsertTrackAtIndex(index, true)
  local track = reaper.GetTrack(0, index)
  reaper.GetSetMediaTrackInfo_String(track, "P_NAME", name, true)
  if volume_db then
    reaper.SetMediaTrackInfo_Value(track, "D_VOL", 10 ^ (volume_db / 20))
  end
  if pan then
    reaper.SetMediaTrackInfo_Value(track, "D_PAN", pan)
  end
  local native = parse_color(color)
  if native then
    reaper.SetTrackColor(track, native)
  end
  tracks_by_name[name] = track
  return track
end

local function find_track(name)
  if tracks_by_name[name] then return tracks_by_name[name] end
  for i = 0, reaper.CountTracks(0) - 1 do
    local track = reaper.GetTrack(0, i)
    local _, existing = reaper.GetSetMediaTrackInfo_String(track, "P_NAME", "", false)
    if existing == name then
      tracks_by_name[name] = track
      return track
    end
  end
  return nil
end

local function insert_media(track_name, path, position)
  local track = find_track(track_name)
  if not track then
    log("  ! no track named '%s' — skipping %s", track_name, path)
    return false
  end
  local file = io.open(path, "rb")
  if not file then
    log("  ! missing file, skipped: %s", path)
    return false
  end
  file:close()

  reaper.SetOnlyTrackSelected(track)
  reaper.SetEditCurPos(position, false, false)
  -- mode 0 = add to the currently selected track at the edit cursor
  reaper.InsertMedia(path, 0)
  return true
end

----------------------------------------------------------------------------
-- main
----------------------------------------------------------------------------

local function build(path)
  local handle = io.open(path, "r")
  if not handle then
    reaper.MB("Could not open:\n" .. path, "rambass", 0)
    return
  end

  reaper.Undo_BeginBlock()
  reaper.ClearConsole()
  log("rambass build: %s", path)

  local counts = { TRACK = 0, ITEM = 0, MIDI = 0, MARKER = 0, REGION = 0, TEMPO = 0 }
  local marker_index = 1

  for raw in handle:lines() do
    local line = trim(raw)
    if line ~= "" and line:sub(1, 1) ~= "#" then
      local f = split_tabs(line)
      local kind = f[1]

      if kind == "PROJECT" then
        log("project: %s  %s BPM %s/%s", f[2] or "?", f[3] or "?", f[4] or "4", f[5] or "4")
        reaper.GetSetProjectInfo(0, "PROJECT_SRATE_USE", 1, true)

      elseif kind == "TEMPO" then
        local position = tonum(f[2], 0)
        local bpm = tonum(f[3], 120)
        local num = tonum(f[4], 4)
        local den = tonum(f[5], 4)
        if position <= 0.000001 then
          reaper.SetCurrentBPM(0, bpm, false)
          reaper.SetTempoTimeSigMarker(0, -1, 0, -1, -1, bpm, num, den, false)
        else
          reaper.SetTempoTimeSigMarker(0, -1, position, -1, -1, bpm, num, den, false)
        end
        counts.TEMPO = counts.TEMPO + 1

      elseif kind == "TRACK" then
        add_track(f[2], tonum(f[3], 0), tonum(f[4], 0), f[5])
        counts.TRACK = counts.TRACK + 1

      elseif kind == "MUTE" then
        local track = find_track(f[2])
        if track then
          reaper.SetMediaTrackInfo_Value(track, "B_MUTE", tonum(f[3], 1))
        else
          log("  ! no track named '%s' to mute", tostring(f[2]))
        end

      elseif kind == "ITEM" or kind == "MIDI" then
        if insert_media(f[2], f[3], tonum(f[4], 0)) then
          counts[kind] = counts[kind] + 1
        end

      elseif kind == "MARKER" then
        reaper.AddProjectMarker2(0, false, tonum(f[2], 0), 0, f[3] or "", marker_index, 0)
        marker_index = marker_index + 1
        counts.MARKER = counts.MARKER + 1

      elseif kind == "REGION" then
        reaper.AddProjectMarker2(
          0, true, tonum(f[2], 0), tonum(f[3], 0), f[4] or "", marker_index, 0
        )
        marker_index = marker_index + 1
        counts.REGION = counts.REGION + 1

      elseif kind == "NOTE" then
        log("note: %s", f[2] or "")

      else
        log("  ? unknown record '%s' — ignored", tostring(kind))
      end
    end
  end
  handle:close()

  reaper.UpdateTimeline()
  reaper.UpdateArrange()
  reaper.Undo_EndBlock("rambass: build song", -1)

  log("done — %d tracks, %d audio items, %d MIDI items, %d tempo points, "
      .. "%d markers, %d regions",
      counts.TRACK, counts.ITEM, counts.MIDI, counts.TEMPO, counts.MARKER, counts.REGION)
end

local function main()
  -- Default to reaper/build/ next to this script, two directories up.
  local script_path = ({ reaper.get_action_context() })[2]
  local script_dir = script_path:match("^(.*)[/\\][^/\\]*$") or ""
  local default = script_dir .. "/../build/"

  local ok, chosen = reaper.GetUserFileNameForRead(default, "Pick a rambass build script", "rbs")
  if not ok then return end
  build(chosen)
end

main()
