# Gramps injects register, TOOL, TOOL_UTILS, TOOL_MODE_GUI, STABLE and _ while it loads
# addon registrations.  Do not import them: that would make this file fail during discovery.
register(
    TOOL,
    id="GenSuiteCompanion",
    name=_("Gen Suite Companion"),
    description=_("Sincroniza de forma revisable un árbol de Gramps con Gen Suite"),
    version="0.1.0",
    gramps_target_version="6.0",
    status=STABLE,
    fname="GenSuiteCompanion.py",
    authors=["Gen Suite"],
    category=TOOL_UTILS,
    toolclass="GenSuiteCompanion",
    optionclass="GenSuiteCompanionOptions",
    tool_modes=[TOOL_MODE_GUI],
)
