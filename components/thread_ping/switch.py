import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import switch
from esphome.const import CONF_ID, ENTITY_CATEGORY_CONFIG
from . import ThreadPingComponent, thread_ping_ns

CONF_THREAD_PING_ID = "thread_ping_id"

ThreadPingControlSwitch = thread_ping_ns.class_(
    "ThreadPingControlSwitch", switch.Switch, cg.Component
)

CONFIG_SCHEMA = switch.switch_schema(
    ThreadPingControlSwitch,
    entity_category=ENTITY_CATEGORY_CONFIG,
).extend(
    {
        cv.Required(CONF_THREAD_PING_ID): cv.use_id(ThreadPingComponent),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = await switch.new_switch(config)
    await cg.register_component(var, config)
    parent = await cg.get_variable(config[CONF_THREAD_PING_ID])
    cg.add(var.set_parent(parent))
    cg.add(parent.set_control_switch(var))
