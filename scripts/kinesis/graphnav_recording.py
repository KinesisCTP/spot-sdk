#!/usr/bin/env python3
"""Kinesis helpers for recording and downloading GraphNav maps."""

import argparse
import json
import os
from pathlib import Path
import sys
import time

import bosdyn.client
import bosdyn.client.util
from bosdyn.api.graph_nav import graph_nav_pb2, map_pb2
from bosdyn.client.graph_nav import GraphNavClient
from bosdyn.client.recording import GraphNavRecordingServiceClient


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
    sdk = bosdyn.client.create_standard_sdk('KinesisGraphNavRecording')
    robot = sdk.create_robot(hostname)
    bosdyn.client.util.authenticate(robot)
    robot.time_sync.wait_for_sync()
    return robot


def clients(robot):
    return (
        robot.ensure_client(GraphNavClient.default_service_name),
        robot.ensure_client(GraphNavRecordingServiceClient.default_service_name),
    )


def graph_summary(graph):
    return {
        'waypoint_count': len(graph.waypoints),
        'edge_count': len(graph.edges),
        'waypoints': [{
            'name': waypoint.annotations.name,
            'id': waypoint.id,
            'snapshot_id': waypoint.snapshot_id,
        } for waypoint in graph.waypoints],
        'edges': [{
            'from_waypoint': edge.id.from_waypoint,
            'to_waypoint': edge.id.to_waypoint,
            'snapshot_id': edge.snapshot_id,
        } for edge in graph.edges],
    }


def download_graph(graph_client, output):
    output.mkdir(parents=True, exist_ok=True)
    (output / 'waypoint_snapshots').mkdir(exist_ok=True)
    (output / 'edge_snapshots').mkdir(exist_ok=True)

    graph = graph_client.download_graph()
    (output / 'graph').write_bytes(graph.SerializeToString())

    for waypoint in graph.waypoints:
        if waypoint.snapshot_id:
            snapshot = graph_client.download_waypoint_snapshot(waypoint.snapshot_id)
            (output / 'waypoint_snapshots' / waypoint.snapshot_id).write_bytes(
                snapshot.SerializeToString())

    for edge in graph.edges:
        if edge.snapshot_id:
            snapshot = graph_client.download_edge_snapshot(edge.snapshot_id)
            (output / 'edge_snapshots' / edge.snapshot_id).write_bytes(snapshot.SerializeToString())

    summary = graph_summary(graph)
    summary['downloaded_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    (output / 'kinesis_map_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def upload_graph(graph_client, source):
    graph_file = source / 'graph'
    waypoint_dir = source / 'waypoint_snapshots'
    edge_dir = source / 'edge_snapshots'
    if not graph_file.is_file():
        raise FileNotFoundError(f'Missing graph file: {graph_file}')
    if not waypoint_dir.is_dir():
        raise FileNotFoundError(f'Missing waypoint snapshots directory: {waypoint_dir}')
    if not edge_dir.is_dir():
        raise FileNotFoundError(f'Missing edge snapshots directory: {edge_dir}')

    graph = map_pb2.Graph()
    graph.ParseFromString(graph_file.read_bytes())

    waypoint_snapshots = {}
    for waypoint in graph.waypoints:
        if not waypoint.snapshot_id:
            continue
        snapshot_file = waypoint_dir / waypoint.snapshot_id
        snapshot = map_pb2.WaypointSnapshot()
        snapshot.ParseFromString(snapshot_file.read_bytes())
        waypoint_snapshots[snapshot.id] = snapshot

    edge_snapshots = {}
    for edge in graph.edges:
        if not edge.snapshot_id:
            continue
        snapshot_file = edge_dir / edge.snapshot_id
        snapshot = map_pb2.EdgeSnapshot()
        snapshot.ParseFromString(snapshot_file.read_bytes())
        edge_snapshots[snapshot.id] = snapshot

    response = graph_client.upload_graph(
        graph=graph, generate_new_anchoring=not len(graph.anchoring.anchors))

    missing_waypoints = list(response.unknown_waypoint_snapshot_ids)
    missing_edges = list(response.unknown_edge_snapshot_ids)

    try:
        # Probe whether this robot supports batched snapshot upload.
        graph_client.upload_snapshots(
            graph_nav_pb2.UploadSnapshotsRequest.Snapshots(waypoint_snapshots=[],
                                                           edge_snapshots=[]))
        if missing_waypoints:
            graph_client.upload_snapshots(
                graph_nav_pb2.UploadSnapshotsRequest.Snapshots(
                    waypoint_snapshots=[waypoint_snapshots[snapshot_id]
                                        for snapshot_id in missing_waypoints],
                    edge_snapshots=[]))
        if missing_edges:
            graph_client.upload_snapshots(
                graph_nav_pb2.UploadSnapshotsRequest.Snapshots(
                    waypoint_snapshots=[],
                    edge_snapshots=[edge_snapshots[snapshot_id] for snapshot_id in missing_edges]))
    except Exception:
        for snapshot_id in missing_waypoints:
            graph_client.upload_waypoint_snapshot(waypoint_snapshots[snapshot_id])
        for snapshot_id in missing_edges:
            graph_client.upload_edge_snapshot(edge_snapshots[snapshot_id])

    return {
        'waypoint_count': len(graph.waypoints),
        'edge_count': len(graph.edges),
        'uploaded_waypoint_snapshots': len(missing_waypoints),
        'uploaded_edge_snapshots': len(missing_edges),
    }


def cmd_status(args):
    robot = make_robot(args)
    graph_client, recording_client = clients(robot)
    status = recording_client.get_record_status()
    graph = graph_client.download_graph()
    print(f'recording={status.is_recording}')
    print(f'waypoints={len(graph.waypoints)} edges={len(graph.edges)}')


def cmd_list(args):
    robot = make_robot(args)
    graph_client, _ = clients(robot)
    graph = graph_client.download_graph()
    print(f'{len(graph.waypoints)} waypoints, {len(graph.edges)} edges')
    for index, waypoint in enumerate(graph.waypoints, 1):
        print(f'{index:03d} name={waypoint.annotations.name or "<unnamed>"} id={waypoint.id}')


def cmd_download(args):
    robot = make_robot(args)
    graph_client, _ = clients(robot)
    summary = download_graph(graph_client, Path(args.output))
    print(f'downloaded={args.output}')
    print(f'waypoints={summary["waypoint_count"]} edges={summary["edge_count"]}')


def cmd_upload(args):
    if args.confirm != 'UPLOAD_GRAPH_TO_ROBOT':
        print('Refusing to replace active robot graph without --confirm UPLOAD_GRAPH_TO_ROBOT',
              file=sys.stderr)
        return 2
    robot = make_robot(args)
    graph_client, _ = clients(robot)
    summary = upload_graph(graph_client, Path(args.source))
    print(f'uploaded={args.source}')
    print(f'waypoints={summary["waypoint_count"]} edges={summary["edge_count"]}')
    print(f'uploaded_waypoint_snapshots={summary["uploaded_waypoint_snapshots"]}')
    print(f'uploaded_edge_snapshots={summary["uploaded_edge_snapshots"]}')
    return 0


def cmd_clear(args):
    if args.confirm != 'CLEAR_ACTIVE_GRAPH':
        print('Refusing to clear active robot graph without --confirm CLEAR_ACTIVE_GRAPH',
              file=sys.stderr)
        return 2
    robot = make_robot(args)
    graph_client, _ = clients(robot)
    graph_client.clear_graph()
    print('cleared active robot graph')
    return 0


def cmd_start(args):
    robot = make_robot(args)
    _, recording_client = clients(robot)
    metadata = GraphNavRecordingServiceClient.make_client_metadata(
        session_name=args.session, client_username='Kinesis')
    recording_env = GraphNavRecordingServiceClient.make_recording_environment(
        name=args.session,
        waypoint_env=GraphNavRecordingServiceClient.make_waypoint_environment(
            client_metadata=metadata))
    recording_client.start_recording(recording_environment=recording_env)
    print(f'started recording session={args.session}')


def cmd_waypoint(args):
    robot = make_robot(args)
    _, recording_client = clients(robot)
    response = recording_client.create_waypoint(waypoint_name=args.name)
    print(f'created waypoint name={args.name} status={response.status}')


def cmd_stop(args):
    robot = make_robot(args)
    _, recording_client = clients(robot)
    while True:
        try:
            recording_client.stop_recording()
            print('stopped recording')
            return
        except bosdyn.client.recording.NotReadyYetError:
            print('recording service finishing, waiting...')
            time.sleep(1)


def parser():
    default_env = Path(__file__).resolve().parents[2] / '.spot' / 'robot.env'
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument('--env-file', default=str(default_env), help='Spot environment file')
    sub = root.add_subparsers(dest='command', required=True)

    sub.add_parser('status').set_defaults(func=cmd_status)
    sub.add_parser('list').set_defaults(func=cmd_list)

    download = sub.add_parser('download')
    download.add_argument('--output', required=True)
    download.set_defaults(func=cmd_download)

    upload = sub.add_parser('upload')
    upload.add_argument('--source', required=True)
    upload.add_argument('--confirm', required=True)
    upload.set_defaults(func=cmd_upload)

    clear = sub.add_parser('clear')
    clear.add_argument('--confirm', required=True)
    clear.set_defaults(func=cmd_clear)

    start = sub.add_parser('start')
    start.add_argument('--session', default='ad-ports-arena')
    start.set_defaults(func=cmd_start)

    waypoint = sub.add_parser('waypoint')
    waypoint.add_argument('name')
    waypoint.set_defaults(func=cmd_waypoint)

    sub.add_parser('stop').set_defaults(func=cmd_stop)
    return root


def main():
    args = parser().parse_args()
    result = args.func(args)
    if result:
        sys.exit(result)


if __name__ == '__main__':
    main()
