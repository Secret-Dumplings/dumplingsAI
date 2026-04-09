# 1. 准备一个“双键”容器
agent_list = {}          # {key1: cls, key2: cls}

def register_agent(uuid, name, description=None):
    """
    类装饰器：把被装饰的类同时注册到两个字典键下。
    两个键指向**同一个类对象**，因此内存中只有一份。
    """
    def _decorator(cls):
        cls.uuid = uuid
        cls.name = name
        cls.description = description
        cls_instantiation = cls()
        agent_list[uuid] = cls_instantiation
        agent_list[name] = cls_instantiation
        return cls              # 原样返回，不影响类本身
    return _decorator


if __name__ == '__main__':
    # 2. 用法示例
    @register_agent('foo', 'bar')
    class Demo:
        def __init__(self, name):
            self.name = name

        def greet(self):
            return f'Hello {self.name}'

    # 3. 通过任意键都能拿到类
    Cls1 = agent_list['foo']   # 拿到 Demo
    Cls2 = agent_list['bar']   # 拿到同一个 Demo
    assert Cls1 is Cls2              # True

    # 4. 像正常类一样用
    obj = Cls1('world')
    print(obj.greet())               # Hello world