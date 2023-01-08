import json
import redis
from random import shuffle
from fedml.cli.model_deployment.device_server_constants import ServerConstants


class FedMLModelCache(object):
    FEDML_MODEL_DEPLOYMENT_RESULT_TAG = "FEDML_MODEL_DEPLOYMENT_RESULT-"
    FEDML_MODEL_DEPLOYMENT_STATUS_TAG = "FEDML_MODEL_DEPLOYMENT_STATUS-"
    FEDML_MODEL_DEPLOYMENT_MONITOR_TAG = "FEDML_MODEL_DEPLOYMENT_MONITOR-"
    FEDML_KEY_COUNT_PER_SCAN = 1000
    FEDML_ALL_DEVICE_ID_TAG = "fedml-all-devices"

    def __init__(self):
        pass

    def __new__(cls, *args, **kw):
        if not hasattr(cls, "_instance"):
            orig = super(FedMLModelCache, cls)
            cls._instance = orig.__new__(cls, *args, **kw)
            cls._instance.init()
        return cls._instance

    def init(self):
        self.redis_pool = None
        self.redis_connection = None

    def setup_redis_connection(self, redis_addr, redis_port):
        self.redis_pool = redis.ConnectionPool(host=redis_addr, port=int(redis_port), decode_responses=True)
        self.redis_connection = redis.Redis(connection_pool=self.redis_pool)

    @staticmethod
    def get_instance(redis_addr="local", redis_port=6379):
        instance = FedMLModelCache()
        if instance.redis_pool is None:
            if redis_addr is None or redis_addr == "local":
                instance.setup_redis_connection("localhost", redis_port)
            else:
                instance.setup_redis_connection(redis_addr, redis_port)
        return instance

    def set_deployment_result(self, end_point_id, device_id, deployment_result):
        result_dict = {"cache_device_id": device_id, "result": deployment_result}
        result_list = self.get_deployment_result_list(end_point_id)
        for result_item in result_list:
            cache_device_id, _ = self.get_result_item_info(result_item)
            if cache_device_id == device_id:
                self.redis_connection.lrem(self.get_deployment_result_key(end_point_id), 0, result_item)
                break
        self.redis_connection.rpush(self.get_deployment_result_key(end_point_id), json.dumps(result_dict))

    def set_deployment_status(self, end_point_id, device_id, deployment_status):
        status_dict = {"cache_device_id": device_id, "status": deployment_status}
        status_list = self.get_deployment_status_list(end_point_id)
        for status_item in status_list:
            cache_device_id, _ = self.get_status_item_info(status_item)
            if cache_device_id == device_id:
                self.redis_connection.lrem(self.get_deployment_status_key(end_point_id), 0, status_item)
                break
        self.redis_connection.rpush(self.get_deployment_status_key(end_point_id), json.dumps(status_dict))

    def get_deployment_result_list(self, end_point_id):
        result_list = self.redis_connection.lrange(self.get_deployment_result_key(end_point_id), 0, -1)
        return result_list

    def get_deployment_result_list_size(self, end_point_id):
        result_list = self.get_deployment_result_list(end_point_id)
        return len(result_list)

    def get_deployment_status_list(self, end_point_id):
        status_list = self.redis_connection.lrange(self.get_deployment_status_key(end_point_id), 0, -1)
        return status_list

    def get_deployment_status_list_size(self, end_point_id):
        status_list = self.get_deployment_status_list(end_point_id)
        return len(status_list)

    def get_status_item_info(self, status_item):
        status_item_json = json.loads(status_item)
        device_id = status_item_json["cache_device_id"]
        status_payload = status_item_json["status"]
        return device_id, status_payload

    def get_result_item_info(self, result_item):
        result_item_json = json.loads(result_item)
        device_id = result_item_json["cache_device_id"]
        result_payload = result_item_json["result"]
        return device_id, result_payload

    def get_idle_device(self, end_point_id, in_model_id):
        # Check whether the end point is activated.
        end_point_activated = self.get_end_point_status(end_point_id)
        if not end_point_activated:
            return None

        # Find all deployed devices
        status_list = self.get_deployment_status_list(end_point_id)
        idle_device_list = list()
        for status_item in status_list:
            device_id, status_payload = self.get_status_item_info(status_item)
            model_status = status_payload["model_status"]
            model_id = status_payload["model_id"]
            if model_id == in_model_id and model_status == ServerConstants.MSG_MODELOPS_DEPLOYMENT_STATUS_DEPLOYED:
                idle_device_list.append(device_id)

        # Randomly shuffle the list of deployed devices and get the first one as the target idle device.
        if len(idle_device_list) <= 0:
            return None
        shuffle(idle_device_list)
        idle_device_id = idle_device_list[0]

        # Find deployment result from the target idle device.
        result_list = self.get_deployment_result_list(end_point_id)
        for result_item in result_list:
            device_id, result_payload = self.get_result_item_info(result_item)
            model_id = result_payload["model_id"]
            if device_id == idle_device_id and model_id == in_model_id:
                return result_payload

        return None

    def set_end_point_status(self, end_point_id, activate_status):
        end_point_status_dict = {"end_point_status": activate_status}
        self.set_deployment_status(end_point_id, FedMLModelCache.FEDML_ALL_DEVICE_ID_TAG, end_point_status_dict)

    def get_end_point_status(self, end_point_id):
        end_point_activated = False
        status_list = self.get_deployment_status_list(end_point_id)
        for status_item in status_list:
            device_id, status_payload = self.get_status_item_info(status_item)
            if str(device_id) == FedMLModelCache.FEDML_ALL_DEVICE_ID_TAG:
                end_point_activated = status_payload["end_point_status"]
                break

        return end_point_activated

    def get_deployment_result_key(self, end_point_id):
        return "{}{}".format(FedMLModelCache.FEDML_MODEL_DEPLOYMENT_RESULT_TAG, end_point_id)

    def get_deployment_status_key(self, end_point_id):
        return "{}{}".format(FedMLModelCache.FEDML_MODEL_DEPLOYMENT_STATUS_TAG, end_point_id)

    def set_monitor_metrics(self, end_point_id, total_latency, avg_latency,
                            total_request_num, current_qps,
                            avg_qps, timestamp):
        metrics_dict = {"total_latency": total_latency, "avg_latency": avg_latency,
                        "total_request_num": total_request_num, "current_qps": current_qps,
                        "avg_qps": avg_qps, "timestamp": timestamp}
        self.redis_connection.rpush(self.get_monitor_metrics_key(end_point_id), json.dumps(metrics_dict))

    def get_latest_monitor_metrics(self, end_point_id):
        if not self.redis_connection.exists(self.get_monitor_metrics_key(end_point_id)):
            return None

        return self.redis_connection.lindex(self.get_monitor_metrics_key(end_point_id), -1)

    def get_monitor_metrics_item(self, end_point_id, index):
        if not self.redis_connection.exists(self.get_monitor_metrics_key(end_point_id)):
            return None, 0

        metrics_item = self.redis_connection.lindex(self.get_monitor_metrics_key(end_point_id), index)
        return metrics_item, index+1

    def get_metrics_item_info(self, metrics_item):
        metrics_item_json = json.loads(metrics_item)
        total_latency = metrics_item_json["total_latency"]
        avg_latency = metrics_item_json["avg_latency"]
        total_request_num = metrics_item_json["total_request_num"]
        current_qps = metrics_item_json["current_qps"]
        avg_qps = metrics_item_json["avg_qps"]
        timestamp = metrics_item_json["timestamp"]
        return total_latency, avg_latency, total_request_num, current_qps, avg_qps, timestamp

    def get_monitor_metrics_key(self, end_point_id):
        return "{}{}".format(FedMLModelCache.FEDML_MODEL_DEPLOYMENT_MONITOR_TAG, end_point_id)


if __name__ == "__main__":
    _end_point_id_ = "4f63aa70-312e-4a9c-872d-cc6e8d95f95b"
    _status_list_ = FedMLModelCache.get_instance().get_deployment_status_list(_end_point_id_)
    _result_list_ = FedMLModelCache.get_instance().get_deployment_result_list(_end_point_id_)
    idle_result_payload = FedMLModelCache.get_instance().get_idle_device(_end_point_id_)
