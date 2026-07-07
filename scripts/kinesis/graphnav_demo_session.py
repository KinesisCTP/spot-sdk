#!/usr/bin/env python3
"""Persistent Kinesis GraphNav demo session.

The process keeps the body lease, keeps Spot standing while idle, and accepts waypoint
commands from stdin so Spot does not sit between one-shot commands.
"""

import argparse
import os
from pathlib import Path
import sys
import threading
import time

import bosdyn.client
import bosdyn.client.util
from bosdyn.api import robot_state_pb2
from bosdyn.api.graph_nav import graph_nav_pb2
from bosdyn.client.exceptions import LeaseUseError
from bosdyn.client.graph_nav import GraphNavClient
from bosdyn.client.lease import LeaseClient, LeaseKeepAlive
from bosdyn.client.power import PowerClient, power_on_motors
from bosdyn.client.robot_command import RobotCommandBuilder, RobotCommandClient, blocking_stand
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
    sdk = bosdyn.client.create_standard_sdk('KinesisGraphNavDemoSession')
    robot = sdk.create_robot(hostname)
    bosdyn.client.util.authenticate(robot)
    robot.time_sync.wait_for_sync()
    return robot


class DemoSession:
    def __init__(self, robot, args):
        self.robot = robot
        self.args = args
        self.lease_client = robot.ensure_client(LeaseClient.default_service_name)
        self.graph_client = robot.ensure_client(GraphNavClient.default_service_name)
        self.command_client = robot.ensure_client(RobotCommandClient.default_service_name)
        self.state_client = robot.ensure_client(RobotStateClient.default_service_name)
        self.power_client = robot.ensure_client(PowerClient.default_service_name)
        self.stop_event = threading.Event()
        self.navigation_active = threading.Event()
        self.keep_stand_thread = threading.Thread(target=self._keep_standing, daemon=True)

    def start(self):
        if self.args.take_lease:
            print('Taking body lease from current holder...')
            self.lease_client.take()
        self.lease_keepalive = LeaseKeepAlive(
            self.lease_client, must_acquire=True, return_at_exit=True)
        self.ensure_standing()
        self.keep_stand_thread.start()

    def shutdown(self):
        self.stop_event.set()
        self.keep_stand_thread.join(timeout=2)
        self.lease_keepalive.shutdown()

    def ensure_standing(self):
        if self.robot.is_estopped():
            raise RuntimeError('Robot is estopped. Clear or configure e-stop before continuing.')
        state = self.state_client.get_robot_state()
        if state.power_state.motor_power_state != robot_state_pb2.PowerState.STATE_ON:
            print('Powering on motors...')
            power_on_motors(self.power_client)
        print('Standing...')
        blocking_stand(self.command_client, timeout_sec=20)

    def _keep_standing(self):
        while not self.stop_event.is_set():
            if not self.navigation_active.is_set():
                try:
                    command = RobotCommandBuilder.synchro_stand_command()
                    self.command_client.robot_command(command, end_time_secs=time.time() + 6.0)
                except Exception as exc:  # pylint: disable=broad-except
                    print(f'keep_standing_warning={exc}')
            self.stop_event.wait(3.0)

    def waypoint_by_name(self, name):
        graph = self.graph_client.download_graph()
        matches = [waypoint for waypoint in graph.waypoints if waypoint.annotations.name == name]
        if not matches:
            available = ', '.join(sorted(filter(None, (wp.annotations.name for wp in graph.waypoints))))
            raise ValueError(f'Unknown waypoint name {name!r}. Available: {available}')
        if len(matches) > 1:
            raise ValueError(f'Waypoint name {name!r} is ambiguous.')
        return matches[0]

    def where(self):
        localization = self.graph_client.get_localization_state()
        graph = self.graph_client.download_graph()
        current_id = localization.localization.waypoint_id
        current_name = '<unknown>'
        for waypoint in graph.waypoints:
            if waypoint.id == current_id:
                current_name = waypoint.annotations.name or '<unnamed>'
                break
        print(f'where name={current_name} id={current_id or "<none>"}')

    def list_stations(self):
        graph = self.graph_client.download_graph()
        for waypoint in graph.waypoints:
            if waypoint.annotations.name.startswith('station_'):
                print(f'{waypoint.annotations.name} {waypoint.id}')

    def go_to(self, name):
        destination = self.waypoint_by_name(name)
        localization = self.graph_client.get_localization_state()
        if not localization.localization.waypoint_id:
            raise RuntimeError('Robot is not localized to the active GraphNav map.')

        print(f'go_to name={name} id={destination.id}')
        self.navigation_active.set()
        command_id = None
        deadline = time.time() + self.args.timeout
        last_status = None
        try:
            while time.time() < deadline:
                try:
                    command_id = self.graph_client.navigate_to(
                        destination.id, 1.0, command_id=command_id)
                except LeaseUseError:
                    if not self.args.take_lease:
                        raise
                    print('lease_use_error=retaking')
                    self.lease_client.take()
                    command_id = None
                    continue

                time.sleep(self.args.poll_interval)
                feedback = self.graph_client.navigation_feedback(command_id)
                status_name = graph_nav_pb2.NavigationFeedbackResponse.Status.Name(feedback.status)
                if status_name != last_status:
                    print(f'navigation_status={status_name}')
                    last_status = status_name

                if feedback.status == graph_nav_pb2.NavigationFeedbackResponse.STATUS_LEASE_ERROR:
                    if not self.args.take_lease:
                        return feedback.status
                    print('navigation_status=STATUS_LEASE_ERROR retaking')
                    self.lease_client.take()
                    command_id = None
                    continue
                if feedback.status in TERMINAL_STATUSES:
                    return feedback.status
            raise TimeoutError(f'Timed out navigating to {name}.')
        finally:
            self.ensure_standing()
            self.navigation_active.clear()

    def repl(self):
        print('Commands: go station_1|station_2|station_3, where, list, stand, quit')
        for line in sys.stdin:
            command = line.strip()
            if not command:
                continue
            try:
                if command in ('quit', 'exit'):
                    print('quitting')
                    return
                if command == 'where':
                    self.where()
                elif command == 'list':
                    self.list_stations()
                elif command == 'stand':
                    self.ensure_standing()
                elif command.startswith('go '):
                    status = self.go_to(command.split(maxsplit=1)[1])
                    status_name = graph_nav_pb2.NavigationFeedbackResponse.Status.Name(status)
                    print(f'final_navigation_status={status_name}')
                else:
                    print('Unknown command. Use: go <station>, where, list, stand, quit')
            except Exception as exc:  # pylint: disable=broad-except
                print(f'command_error={exc}')
            print('ready')


def parser():
    default_env = Path(__file__).resolve().parents[2] / '.spot' / 'robot.env'
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument('--env-file', default=str(default_env), help='Spot environment file')
    root.add_argument('--timeout', type=float, default=120.0)
    root.add_argument('--poll-interval', type=float, default=0.5)
    root.add_argument('--take-lease', action='store_true')
    return root


def main():
    args = parser().parse_args()
    robot = make_robot(args)
    session = DemoSession(robot, args)
    session.start()
    try:
        session.repl()
    finally:
        session.shutdown()


if __name__ == '__main__':
    main()
