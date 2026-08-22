--[[
  rambass_build_song.lua — build a Reaper project from a rambass build script.

  Usage
  -----
  1. Generate a build script:      rambass reaper build <song>
  2. In Reaper: File > New Project
  3. Actions > Show action list > ReaScript: Run ReaScript (EEL2 or Lua)... >
     pick this file. (Reaper 6 called this action "ReaScript: Load...")
  4. Choose the .rbs file from reaper/build/

  It creates the tracks, imports the count-in / click / backing / drum MIDI /
  reference stems, writes the tempo map and drops a marker and a region on every
  section.

  Two separate count-in tracks, and they are not interchangeable:
    STICKS  the drumstick count-in only. A musical part; may go to the PA.
    CLICK   the click for the song itself, for rehearsal and overdubs.
            Muted on build, and it must stay out of front of house.
  Neither is ever mixed into the backing track.

  Re-runnable. Running it again on a project it built **replaces what it
  generated and keeps what you set up**: tracks it made are reused, so the drum
  VST on DRUMS MIDI, your volumes, mutes and routing all survive, while items,
  the tempo map and the markers are rebuilt from the .rbs. That matters because
  the .rbs changes often — a re-transcribed drum part, a corrected anchor — and
  re-doing the kit every time is how people stop rebuilding and start editing by
  hand instead.

  It only clears things on tracks it owns (tagged P_EXT:rambass), so a track you
  added yourself is left alone entirely.

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
local TAG = "P_EXT:rambass"

local function is_ours(track)
  local _, value = reaper.GetSetMediaTrackInfo_String(track, TAG, "", false)
  return value == "1"
end

local function clear_items(track)
  for i = reaper.CountTrackMediaItems(track) - 1, 0, -1 do
    reaper.DeleteTrackMediaItem(track, reaper.GetTrackMediaItem(track, i))
  end
end

-- Reuse a track of this name if it is already here, so a rebuild keeps the drum
-- VST, the routing and any levels you have set. Volume, pan and colour are only
-- applied when the track is created: after that they are yours, not the build
-- script's.
local function ensure_track(name, volume_db, pan, color)
  for i = 0, reaper.CountTracks(0) - 1 do
    local track = reaper.GetTrack(0, i)
    local _, existing = reaper.GetSetMediaTrackInfo_String(track, "P_NAME", "", false)
    if existing == name and is_ours(track) then
      tracks_by_name[name] = track
      clear_items(track)
      return track, false
    end
  end

  local index = reaper.CountTracks(0)
  reaper.InsertTrackAtIndex(index, true)
  local track = reaper.GetTrack(0, index)
  reaper.GetSetMediaTrackInfo_String(track, "P_NAME", name, true)
  reaper.GetSetMediaTrackInfo_String(track, TAG, "1", true)
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
  return track, true
end

-- Markers, regions and the tempo map are wholly described by the .rbs, so a
-- rebuild starts from a clean slate — but only when this project was built by
-- rambass before. On a project that was not, we add and touch nothing else.
local function clear_generated(previously_built)
  for i = reaper.CountTempoTimeSigMarkers(0) - 1, 0, -1 do
    reaper.DeleteTempoTimeSigMarker(0, i)
  end
  if not previously_built then return end
  local total = reaper.CountProjectMarkers(0)
  for i = total - 1, 0, -1 do
    local ok, isrgn, _, _, _, index = reaper.EnumProjectMarkers(i)
    if ok and ok ~= 0 then
      reaper.DeleteProjectMarker(0, index, isrgn)
    end
  end
end

local function project_was_built_by_us()
  for i = 0, reaper.CountTracks(0) - 1 do
    if is_ours(reaper.GetTrack(0, i)) then return true end
  end
  return false
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

local function insert_media(track_name, path, position, length)
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

  -- An explicit length is only sent for a source Reaper cannot take one from —
  -- a still image, whose length comes from a global preference ("length of
  -- image items") that this project cannot assume anything about. InsertMedia
  -- leaves the new item selected, so it is the one to resize.
  if length and length > 0 then
    local item = reaper.GetSelectedMediaItem(0, 0)
    if item then
      reaper.SetMediaItemInfo_Value(item, "D_LENGTH", length)
    else
      log("  ! could not find the item just inserted, length not set: %s", path)
    end
  end
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

  local counts = { TRACK = 0, ITEM = 0, MIDI = 0, MARKER = 0, REGION = 0, TEMPO = 0,
                   CREATED = 0, REUSED = 0 }
  local marker_index = 1
  local fresh_tracks = {}

  local rebuilding = project_was_built_by_us()
  clear_generated(rebuilding)
  if rebuilding then
    log("rebuilding in place — keeping existing tracks, their FX and levels")
  end

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
        local _, created = ensure_track(f[2], tonum(f[3], 0), tonum(f[4], 0), f[5])
        counts.TRACK = counts.TRACK + 1
        if created then
          counts.CREATED = counts.CREATED + 1
          fresh_tracks[f[2]] = true
        else
          counts.REUSED = counts.REUSED + 1
        end

      elseif kind == "MUTE" then
        local track = find_track(f[2])
        if not track then
          log("  ! no track named '%s' to mute", tostring(f[2]))
        elseif fresh_tracks[f[2]] then
          reaper.SetMediaTrackInfo_Value(track, "B_MUTE", tonum(f[3], 1))
        end
        -- On a rebuild the mute state is yours: if you unmuted CLICK to work
        -- against it, re-running the build should not silently re-mute it.

      elseif kind == "ITEM" or kind == "MIDI" then
        if insert_media(f[2], f[3], tonum(f[4], 0), tonum(f[5], 0)) then
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

  log("done — %d tracks (%d new, %d reused), %d audio items, %d MIDI items, "
      .. "%d tempo points, %d markers, %d regions",
      counts.TRACK, counts.CREATED, counts.REUSED, counts.ITEM, counts.MIDI,
      counts.TEMPO, counts.MARKER, counts.REGION)
end

local function main()
  -- Default to reaper/build/, which sits beside reaper/scripts/.
  --
  -- Resolve it properly rather than handing the dialog a path with ".." in the
  -- middle: Windows does not resolve that, and silently opens whatever folder
  -- you were last in — which looks exactly like the .rbs not existing.
  local script_path = ({ reaper.get_action_context() })[2]
  local script_dir = script_path:match("^(.*)[/\\][^/\\]*$") or ""
  local sep = package.config:sub(1, 1)
  local root = script_dir:gsub("[/\\][sS]cripts$", "")
  local build_dir = root .. sep .. "build" .. sep

  -- Pre-select a build script if one is there, so the dialog opens *on* it.
  local default = build_dir
  local i = 0
  while true do
    local name = reaper.EnumerateFiles(build_dir, i)
    if not name then break end
    if name:lower():match("%.rbs$") then
      default = build_dir .. name
      break
    end
    i = i + 1
  end

  -- RAMBASS_RBS skips the dialog, so a rebuild can be driven from a script or a
  -- test harness rather than by hand.
  local preset = os.getenv("RAMBASS_RBS")
  if preset and preset ~= "" then
    build(preset)
    return
  end

  local ok, chosen = reaper.GetUserFileNameForRead(default, "Pick a rambass build script", "rbs")
  if not ok then return end
  build(chosen)
end

main()
