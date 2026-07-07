# AD Ports Robotic Awareness Spot Workflow

This branch keeps AD Ports event development separate from the upstream Boston Dynamics SDK. Kinesis users should treat this repo as the SDK working copy plus Kinesis helper scripts and documentation.

Start with read-only checks. Do not run examples that acquire a lease, power motors, command motion, dock, undock, or manipulate the arm until the event operator confirms the robot is in a safe test area.

## Repository Layout

- `python/examples/`: upstream Boston Dynamics SDK examples.
- `scripts/kinesis/`: Kinesis helper scripts that wrap SDK examples with local configuration.
- `docs/kinesis/`: Kinesis workflow documentation.
- `.spot/`: local robot connection profiles. Real `.env` files are ignored by git.

## Branch

Use this branch for event work:

```bash
git checkout ad-ports-robotic-awareness
```

Keep `master` close to the upstream SDK. Put event-specific helpers, docs, and experiments on this branch or on branches created from it.

## Robot Profile

This workflow was tested with:

- Robot: `spot-BD-02930002`
- Spot access-point address: `192.168.80.3`
- Local credentials file: `.spot/robot.env`

The robot name may not resolve through local DNS. On the Spot access-point Wi-Fi, use `SPOT_HOSTNAME=192.168.80.3`.

## Credentials

Boston Dynamics SDK examples authenticate through environment variables:

- `BOSDYN_CLIENT_USERNAME`
- `BOSDYN_CLIENT_PASSWORD`

Kinesis helper scripts also read:

- `SPOT_HOSTNAME`

Create a local credentials file from the template:

```bash
cp .spot/robot.env.example .spot/robot.env
chmod 600 .spot/robot.env
```

Edit `.spot/robot.env` locally:

```bash
SPOT_HOSTNAME=192.168.80.3
BOSDYN_CLIENT_USERNAME=<robot-user>
BOSDYN_CLIENT_PASSWORD=<robot-password>
```

Rules for users:

- Do not commit `.spot/robot.env`.
- Do not paste credentials into shell history, tickets, chat, notebooks, or docs.
- Keep file permissions at `600`.
- Use `SPOT_ENV_FILE=/path/to/profile.env` if you need multiple robot profiles.

Example with an alternate profile:

```bash
SPOT_ENV_FILE=.spot/ad-ports.env scripts/kinesis/get_robot_state.sh state
```

## Python Dependencies

Install the read-only robot-state dependencies into a repo-local folder:

```bash
scripts/kinesis/install_robot_state_deps.sh
```

This creates `.python-deps/`, which is ignored by git. The Kinesis runner automatically adds that folder to `PYTHONPATH` when it exists.

This approach avoids changing system Python packages and works on machines where `python3 -m venv` is unavailable. If you prefer a virtual environment, create and activate it first, then install the SDK example requirements in the standard way.

## Network Check

Connect to the Spot access-point Wi-Fi or another network that can reach the robot.

For this robot, check the default Spot AP address:

```bash
ping -c 1 192.168.80.3
```

If ping fails, fix network connectivity before running SDK examples.

## Read-Only Smoke Test

Use the robot-state example before any motion or lease-taking commands:

```bash
scripts/kinesis/get_robot_state.sh state
```

Supported read-only commands from the SDK example:

```bash
scripts/kinesis/get_robot_state.sh hardware
scripts/kinesis/get_robot_state.sh metrics
scripts/kinesis/get_robot_state.sh joints
scripts/kinesis/get_robot_state.sh frame_tree
```

Expected first-test signs:

- The script authenticates without prompting.
- Robot state prints to the terminal.
- Power, battery, estop, kinematic, and fault sections are visible.

The first successful test on this branch connected through `192.168.80.3`. The robot reported battery at 100%, robot power on, motor power off, shore power connected, and software/hardware estops not estopped.

## Troubleshooting

If authentication fails:

- Check `BOSDYN_CLIENT_USERNAME` and `BOSDYN_CLIENT_PASSWORD` in `.spot/robot.env`.
- Confirm the robot account works through the robot admin interface or with the event operator.

If the SDK says the robot is unreachable:

- Confirm Wi-Fi/network connection.
- Use `SPOT_HOSTNAME=192.168.80.3` on the Spot AP network.
- Run `ping -c 1 192.168.80.3`.

If Python imports fail:

- Re-run `scripts/kinesis/install_robot_state_deps.sh`.
- Confirm `.python-deps/` exists.
- Run from the repo root.

If a future user needs to run motion examples:

- Confirm the robot is in a safe area.
- Start with the Boston Dynamics e-stop example.
- Confirm lease ownership, estop status, and operator responsibility before commanding movement.
- Document any new helper script in `docs/kinesis/` before sharing it.
