#!/usr/bin/python
# -*- coding: UTF-8 -*-

import functools
import importlib
import inspect
import json
import logging
import sys
import time
import traceback
import uuid
from concurrent import futures

import common_pb2
import common_pb2_grpc
import grpc
from google.protobuf import json_format

logger = logging.getLogger(__name__)
rpc_logger = logging.getLogger("rpc_log")


def rpc_log(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        response = func(*args, **kwargs)
        process_time = time.time() - start_time
        func_name = "{}.{}".format(func.__module__, func.__name__)
        sig = inspect.signature(func)
        bind = sig.bind(*args, **kwargs).arguments
        request = bind.get("request")
        json_data = json_format.MessageToJson(response, preserving_proto_field_name=True)
        if len(json_data) > 1024 * 4:
            rpc_logger.debug("{}".format({
                "type": "rpc_log",
                "request_id": request.request_id,
                "function": func_name,
                "process_time": "{:.6f}s".format(process_time),
                "response": json_data[:1024 * 4] + "...",
            }))
        else:
            rpc_logger.debug("{}".format({
                "type": "rpc_log",
                "request_id": request.request_id,
                "function": func_name,
                "status": response.status,
                "process_time": "{:.6f}s".format(process_time),
                "response": json.loads(json_data),
            }))
        return response

    return wrapper


class Server:

    def __init__(self, server, host, port):
        self.server = server
        self.host = host
        self.port = port
        self.addr = host + ':' + str(port)


class CommonService(common_pb2_grpc.CommonServiceServicer):

    @classmethod
    def clazz_handler(cls, clazz):
        return clazz

    @rpc_log
    def handle(self, request, context):
        request_str = request.request.decode('utf-8')
        grpc_request = json.loads(request_str)
        response = {
            'status': 0,
            'message': "",
            'excType': "",
            'result': "",
        }
        clazz = grpc_request.get('clazz')
        _clazz = self.clazz_handler(clazz)
        module = importlib.import_module(_clazz)
        method = grpc_request.get('method')
        invoke = functools.reduce(lambda x, y: getattr(x, y), [module, *method.split('.')])
        args = grpc_request.get('args') or ()
        kwargs = grpc_request.get('kwargs') or {}
        try:
            response['result'] = invoke(*args, **kwargs)
        except Exception as e:
            exc_type, exc_value, exc_tb = sys.exc_info()
            traceback.print_exception(exc_type, exc_value, exc_tb)
            response['status'] = -1
            response['message'] = str(e)
            response['excType'] = exc_type.__name__

        return common_pb2.CommonResponse(
            response=json.dumps(response, ensure_ascii=False).encode("utf-8"),
            status=response["status"]
        )


class GrpcClient:

    def handle(self):
        pass

    def connect(self, server):
        """
        get server stub
        :param server:
        :return:
        """
        return self.stubs.get(server)

    def load(self, servers):
        """
        load grpc server list
        :param servers: Server
        :return:
        """
        for server in servers:
            channel = grpc.insecure_channel(server.addr)
            stub = common_pb2_grpc.CommonServiceStub(channel)
            self.stubs[server.server] = stub

    def __init__(self):
        self.stubs = {}


class GrpcServer:
    def __init__(self, host='0.0.0.0', port=6565, max_workers=10):
        self.address = host + ':' + str(port)
        self.max_workers = max_workers
        self.service = CommonService()

    def set_clazz_handler(self, func):
        if callable(func):
            self.service.clazz_handler = func

    def run(self):
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=self.max_workers))
        common_pb2_grpc.add_CommonServiceServicer_to_server(self.service, server)
        server.add_insecure_port(self.address)
        server.start()
        logger.info('grpc server running, listen on ' + self.address)
        try:
            while True:
                time.sleep(60 * 60 * 24)
        except KeyboardInterrupt:
            server.stop(0)


class GrpcException(Exception):

    def __init__(self, exc_type, message):
        self.exc_type = exc_type
        self.message = message


def grpc_service(server, serialize=3):
    """
    grpc service define
    :param server: server name
    :param serialize: serialize type, default 3 : json
    :return:
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            sig = inspect.signature(func)
            bind = sig.bind(*args, **kwargs).arguments
            if sig.parameters.get("cls"):
                cls = bind.get("cls")
                bind.pop("cls")
            request = {
                'clazz': func.__module__,
                'method': func.__qualname__,
                'args': (),
                'kwargs': dict(bind.items()),
            }
            request_json = json.dumps(request, ensure_ascii=False)
            response = grpc_client.connect(server).handle(
                common_pb2.CommonRequest(
                    request=request_json.encode('utf-8'),
                    serialize=serialize,
                    request_id=uuid.uuid4().hex
                )
            )
            response_json = json.loads(response.response)
            if response_json.get('status') == 0:
                return response_json.get('result')
            elif response_json.get('status') == -1:
                raise GrpcException(response_json.get('excType'), response_json.get('message'))
            else:
                raise Exception('unknown grpc exception')

        return wrapper

    return decorator


grpc_client = GrpcClient()
