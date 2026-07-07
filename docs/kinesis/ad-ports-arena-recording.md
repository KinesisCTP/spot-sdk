# AD Ports Arena GraphNav Recording

This workflow creates a new event-specific GraphNav map without relying on older maps that may already be loaded on Spot.

## Safety Boundary

Recording a new map changes the active GraphNav map on the robot. Before clearing or recording, back up any active map:

```bash
scripts/kinesis/graphnav_recording.sh download --output maps/backups/pre-ad-ports-active.walk
```

Map data under `maps/` is ignored by git.

Restore a saved map to the robot if needed:

```bash
scripts/kinesis/graphnav_recording.sh upload \
  --source maps/backups/pre-ad-ports-active.walk \
  --confirm UPLOAD_GRAPH_TO_ROBOT
```

## Operator Setup

The robot is expected to start docked. Before recording:

- Confirm the arena is clear.
- Confirm an operator has the tablet and can stop the robot.
- Confirm the robot is connected through `.spot/robot.env`.
- Put a fiducial/AprilTag near the starting area if possible, so the robot can localize later.

## Create A New Arena Map

After the current map has been backed up, clear the active GraphNav map:

```bash
scripts/kinesis/graphnav_recording.sh clear --confirm CLEAR_ACTIVE_GRAPH
```

Start recording:

```bash
scripts/kinesis/graphnav_recording.sh start --session ad-ports-arena
```

Use the tablet to undock and drive the robot through the arena. At each demo station, stop the robot and create a named waypoint from this machine:

```bash
scripts/kinesis/graphnav_recording.sh waypoint station_1
scripts/kinesis/graphnav_recording.sh waypoint station_2
scripts/kinesis/graphnav_recording.sh waypoint station_3
```

When the route is complete, stop recording:

```bash
scripts/kinesis/graphnav_recording.sh stop
```

Download the completed map:

```bash
scripts/kinesis/graphnav_recording.sh download --output maps/ad-ports-arena.walk
```

Inspect the waypoint names and IDs:

```bash
scripts/kinesis/graphnav_recording.sh list
```

The downloaded map includes `kinesis_map_summary.json`, which records waypoint names and full GraphNav IDs for later navigation tooling.

## Demo Navigation Session

For the live demo, use the persistent session instead of one-shot commands. The session keeps the lease and sends periodic stand commands while idle, so Spot remains standing between waypoint moves.

Start the session:

```bash
scripts/kinesis/graphnav_demo_session.sh --take-lease
```

Available session commands:

```text
list
where
go station_1
go station_2
go station_3
stand
quit
```

Use `quit` to release the lease and end the session.

One-shot navigation is still available for testing:

```bash
scripts/kinesis/graphnav_go_to.sh --take-lease --stand-after station_2
```

The one-shot command exits after the move, so it should not be used for demos where Spot must stay standing while waiting for the next command.
