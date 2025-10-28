import hashlib
import time
import random
import string
from urllib.parse import urlencode


class RequestConfig:

    def generate_random_string(self, length=16):
        """生成随机字符串"""
        chars = string.ascii_letters + string.digits
        return "".join(random.choices(chars, k=length))

    def get_opt(self):
        """生成请求参数 opt"""
        truncated_timestamp = int(time.time())
        return {
            "AppId": "q8mziWq3zcgQLUh8",
            "Mode": "normal",
            "NonceStr": self.generate_random_string(16),
            "Timestamp": truncated_timestamp,
            "key": "MjNTazzrYispfNu7yn",
        }

    def generate_sign(self, opt):
        """生成签名 Sign"""
        query_string = self.object_to_query_string(opt)
        md5_hash = hashlib.md5(query_string.encode("utf-8")).hexdigest()
        return md5_hash

    def object_to_query_string(self, obj):
        """将字典转换为 URL 查询字符串"""
        return urlencode(obj)
