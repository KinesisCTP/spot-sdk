#!/usr/bin/env python3
"""Navigate Spot to a named GraphNav waypoint from the active robot map."""

import argparse
import os
from pathlib import Path
import sys
import time

import bosdyn.client
import bosdyn.client.util
from bosdyn.api import robot_state_pb2
from bosdyn.api.graph_nav import graph_nav_pb2
from bosdyn.client.exceptions import LeaseUseError
from bosdyn.client.graph_nav import GraphNavClient
from bosdyn.client.lease import LeaseClient, LeaseKeepAlive, ResourceAlreadyClaimedError
from bosdyn.client.power import PowerClient, power_on_motors
from bosdyn.client.robot_command import RobotCommandClient, blocking_stand
from bosdyn.client.robot_state import RobotStateClient


TERMINAL_STATUSES = {
    graph_nav_pb2.NavigationFeedbackResponse.STATUS_REACHED_GOAL,
    graph_nav_pb2.NavigationFeedbackResponse.STATUS_LOST,
    graph_nav_pb2.NavigationFeedbackResponse.STATUS_STUCK,
    graph_nav_pb2.NavigationFeedbackResponse.STATUS_ROBOT_IMPAIRED,
}


def load_env_file(path):
    if not path.exists():
        raise FileNotFoundError(f'Missing environment file: {path}')
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def make_robot(args):
    load_env_file(Path(args.env_file))
    hostname = os.environ.get('SPOT_HOSTNAME')
    if not hostname:
        raise RuntimeError(f'SPOT_HOSTNAME is not set in {args.env_file}')
    sdk = bosdyn.client.create_standard_sdk('KinesisGraphNavGoTo')
    robot = sdk.create_robot(hostname)
    bosdyn.client.util.authenticate(robot)
    robot.time_sync.wait_for_sync()
    return robot


def resolve_waypoint(graph, name):
    matches = [waypoint for waypoint in graph.waypoints if waypoint.annotations.name == name]
    if not matches:
        available = ', '.join(sorted(filter(None, (wp.annotations.name for wp in graph.waypoints))))
        raise ValueError(f'Unknown waypoint name {name!r}. Available names: {available}')
    if len(matches) > 1:
        ids = ', '.join(waypoint.id for waypoint in matches)
        raise ValueError(f'Waypoint name {name!r} is ambiguous: {ids}')
    return matches[0]


def ensure_ready(robot, args):
    if robot.is_estopped():
        raise RuntimeError('Robot is estopped. Clear or configure an external e-stop first.')

    state_client = robot.ensure_client(RobotStateClient.default_service_name)
    power_client = robot.ensure_client(PowerClient.default_service_name)
    command_client = robot.ensure_client(RobotCommandClient.default_service_name)

    state = state_client.get_robot_state()
    started_powered = state.power_state.motor_power_state == robot_state_pb2.PowerState.STATE_ON
    if not started_powered:
        if args.no_power_on:
            raise RuntimeError('Motor power is off and --no-power-on was set.')
        print('Powering on motors...')
        power_on_motors(power_client)
        print('Standing...')
        blocking_stand(command_client, timeout_sec=20)
    return started_powered


def stand_after_arrival(robot, args):
    if not args.stand_after:
        return

    state_client = robot.ensure_client(RobotStateClient.default_service_name)
    power_client = robot.ensure_client(PowerClient.default_service_name)
    command_client = robot.ensure_client(RobotCommandClient.default_service_name)

    state = state_client.get_robot_state()
    if state.power_state.motor_power_state != robot_state_pb2.PowerState.STATE_ON:
        print('Powering motors back on for stand-after-arrival...')
        power_on_motors(power_client)

    print('Commanding stand after arrival...')
    blocking_stand(command_client, timeout_sec=20)

    if args.hold_stand_seconds > 0:
        print(f'holding_stand_seconds={args.hold_stand_seconds}')
        deadline = time.time() + args.hold_stand_seconds
        while time.time() < deadline:
            blocking_stand(command_client, timeout_sec=5)
            time.sleep(min(5.0, max(0.0, deadline - time.time())))


def navigate(robot, waypoint_name, args):
    lease_client = robot.ensure_client(LeaseClient.default_service_name)
    graph_client = robot.ensure_client(GraphNavClient.default_service_name)
    graph = graph_client.download_graph()
    destination = resolve_waypoint(graph, waypoint_name)

    localization = graph_client.get_localization_state()
    if not localization.localization.waypoint_id:
        raise RuntimeError('Robot is not localized to the active GraphNav map.')

    print(f'Current localization waypoint: {localization.localization.waypoint_id}')
    print(f'Navigating to {waypoint_name}: {destination.id}')

    command_id = None
    deadline = time.time() + args.timeout
    last_status_name = None

    while time.time() < deadline:
        try:
            command_id = graph_client.navigate_to(destination.id, 1.0, command_id=command_id)
        except LeaseUseError:
            if not args.take_lease:
                raise
            print('lease_use_error=retaking')
            lease_client.take()
            command_id = None
            continue
        time.sleep(args.poll_interval)
        feedback = graph_client.navigation_feedback(command_id)
        status_name = graph_nav_pb2.NavigationFeedbackResponse.Status.Name(feedback.status)
        if status_name != last_status_name:
            print(f'navigation_status={status_name}')
            last_status_name = status_name
        if feedback.status == graph_nav_pb2.NavigationFeedbackResponse.STATUS_LEASE_ERROR:
            if not args.take_lease:
                return feedback.status
            print('navigation_status=STATUS_LEASE_ERROR retaking')
            lease_client.take()
            command_id = None
            continue
        if feedback.status in TERMINAL_STATUSES:
            return feedback.status

    raise TimeoutError(f'Timed out navigating to {waypoint_name} after {args.timeout} seconds.')


def parser():
    default_env = Path(__file__).resolve().parents[2] / '.spot' / 'robot.env'
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument('waypoint_name')
    root.add_argument('--env-file', default=str(default_env), help='Spot environment file')
    root.add_argument('--timeout', type=float, default=120.0)
    root.add_argument('--poll-interval', type=float, default=0.5)
    root.add_argument('--no-power-on', action='store_true')
    root.add_argument('--take-lease', action='store_true',
                      help='Forcefully take the body lease from another client before navigating.')
    root.add_argument('--stand-after', action='store_true',
                      help='Command Spot to stand after reaching the destination.')
    root.add_argument('--hold-stand-seconds', type=float, default=0.0,
                      help='Keep the lease and periodically refresh stand after arrival.')
    return root


def main():
    args = parser().parse_args()
    robot = make_robot(args)
    lease_client = robot.ensure_client(LeaseClient.default_service_name)
    try:
        if args.take_lease:
            print('Taking body lease from current holder...')
            lease_client.take()
        with LeaseKeepAlive(lease_client, must_acquire=True, return_at_exit=True):
            ensure_ready(robot, args)
            status = navigate(robot, args.waypoint_name, args)
            if status == graph_nav_pb2.NavigationFeedbackResponse.STATUS_REACHED_GOAL:
                stand_after_arrival(robot, args)
    except ResourceAlreadyClaimedError:
        print('The robot lease is already held by another client. Release tablet control and retry.',
              file=sys.stderr)
        return 2

    status_name = graph_nav_pb2.NavigationFeedbackResponse.Status.Name(status)
    print(f'final_navigation_status={status_name}')
    return 0 if status == graph_nav_pb2.NavigationFeedbackResponse.STATUS_REACHED_GOAL else 1


if __name__ == '__main__':
    sys.exit(main())
