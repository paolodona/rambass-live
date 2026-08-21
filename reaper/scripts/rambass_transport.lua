--[[
  rambass_transport.lua — live transport helpers, meant to be bound to MIDI.

  During the show the laptop should never need a keyboard. Bind these to
  footswitches (or to GX-100 memory switches, which transmit program change on
  the TX channel) via Reaper: Actions > Show action list > find the script >
  "Add..." under Shortcuts, or the MIDI learn button.

  Set ACTION below, save a copy per function, and load each copy as its own
  action. Reaper identifies scripts by filename, so one file = one bindable
  action.

  ACTION values:
    "play_next"   stop, jump to the start of the next region, play
    "play_again"  jump back to the start of the current region and play
    "stop_panic"  stop transport and send all-notes-off on every track
--]]

local ACTION = "play_next"

local function region_bounds()
  local position = reaper.GetPlayState() > 0 and reaper.GetPlayPosition()
    or reaper.GetCursorPosition()
  local _, num_markers, num_regions = reaper.CountProjectMarkers(0)
  local best_start, best_end, best_name = nil, nil, nil
  local next_start, next_name = nil, nil

  for i = 0, num_markers + num_regions - 1 do
    local ok, is_region, start, finish, name = reaper.EnumProjectMarkers(i)
    if ok and is_region then
      if start <= position + 0.001 and position < finish then
        best_start, best_end, best_name = start, finish, name
      end
      if start > position + 0.001 and (next_start == nil or start < next_start) then
        next_start, next_name = start, name
      end
    end
  end
  return best_start, best_end, best_name, next_start, next_name
end

local function panic()
  reaper.Main_OnCommand(1016, 0)                       -- Transport: Stop
  reaper.Main_OnCommand(40345, 0)                      -- send all-notes-off
end

local function play_at(position, label)
  reaper.Main_OnCommand(1016, 0)                       -- Stop
  reaper.SetEditCurPos(position, true, true)
  reaper.Main_OnCommand(1007, 0)                       -- Play
  if label then
    reaper.Help_Set("rambass: " .. label, false)
  end
end

local current_start, _, current_name, next_start, next_name = region_bounds()

if ACTION == "play_next" then
  if next_start then
    play_at(next_start, "next: " .. (next_name or ""))
  else
    reaper.Help_Set("rambass: no further region — end of set", false)
  end
elseif ACTION == "play_again" then
  play_at(current_start or 0, "again: " .. (current_name or "start"))
elseif ACTION == "stop_panic" then
  panic()
else
  reaper.MB("Unknown ACTION: " .. tostring(ACTION), "rambass", 0)
end
