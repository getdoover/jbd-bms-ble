from pydoover.tags import AnyChange, Delta, Tag, Tags


class JbdBmsBleTags(Tags):
    """Everything the app publishes. UI elements bind to these directly."""

    # Link health. Defaults True so a freshly deployed app does not flash a
    # warning before its first poll has had a chance to complete.
    comms_ok = Tag("boolean", default=True, live=True, log_on=AnyChange())
    device_address = Tag("string", default="")

    # Pack telemetry
    pack_voltage = Tag("number", default=None, live=True, log_on=Delta(amount=0.05))
    current = Tag("number", default=None, live=True, log_on=Delta(amount=0.5))
    power = Tag("number", default=None, live=True, log_on=Delta(amount=10))
    soc = Tag("number", default=None, live=True, log_on=Delta(amount=1))
    remaining_ah = Tag("number", default=None, live=True, log_on=Delta(amount=0.5))
    nominal_ah = Tag("number", default=None, log_on=Delta(amount=0.5))
    cycles = Tag("number", default=None, log_on=Delta(amount=1))
    temperature = Tag("number", default=None, live=True, log_on=Delta(amount=0.5))
    temperatures = Tag("array", default=[])
    charge_state = Tag("string", default="unknown", log_on=AnyChange())

    # Cells
    cell_count = Tag("number", default=None)
    cell_voltages = Tag("array", default=[])
    cell_min = Tag("number", default=None, live=True, log_on=Delta(amount=5))
    cell_max = Tag("number", default=None, live=True, log_on=Delta(amount=5))
    cell_delta = Tag("number", default=None, live=True, log_on=Delta(amount=5))
    cells_text = Tag("string", default="")
    balancing = Tag("string", default="none")

    # Protection and switches
    protection_bits = Tag("number", default=0)
    protection_text = Tag("string", default="none", log_on=AnyChange())
    protection_clear = Tag("boolean", default=True, live=True, log_on=AnyChange())
    charge_fet = Tag("boolean", default=True, live=True, log_on=AnyChange())
    discharge_fet = Tag("boolean", default=True, live=True, log_on=AnyChange())
    soc_ok = Tag("boolean", default=True, live=True, log_on=AnyChange())

    # Identity
    hardware_version = Tag("string", default="")
    software_version = Tag("string", default="")
    manufacture_date = Tag("string", default="")
    last_read_text = Tag("string", default="never")

    # Configuration registers, keyed by jbd.RegisterSpec.key
    settings = Tag("object", default={})
    settings_read_text = Tag("string", default="never")
    settings_status = Tag("string", default="")

    scan_results = Tag("string", default="")
