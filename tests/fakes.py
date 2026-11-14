class FakeClient:
    def __init__(self, keys=()):
        self.objects = {k: {} for k in keys}
        self.deleted = []

    def put_object(self, **kw):
        self.objects[kw["Key"]] = kw

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        client = self

        class P:
            def paginate(self, Bucket, Prefix):
                keys = [k for k in client.objects if k.startswith(Prefix)]
                yield {"Contents": [{"Key": k} for k in keys]} if keys else {}

        return P()

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.deleted.append(o["Key"])
            self.objects.pop(o["Key"], None)
