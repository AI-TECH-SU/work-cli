from __future__ import annotations

import ctypes

from workspace_manager.errors import WorkError


class _AEDesc(ctypes.Structure):
    # AEDataModel.h declares AEDesc under #pragma pack(push, 2).
    _pack_ = 2
    _fields_ = [('descriptor_type', ctypes.c_uint32), ('data_handle', ctypes.c_void_p)]


def terminal_automation_permission(*, ask_user: bool = False) -> int:
    """Check permission for Terminal's do-script event without sending it.

    Some events (including reading an application's version) are exempt from
    Automation permission and cannot establish that commands are authorized.
    """
    try:
        framework = ctypes.CDLL('/System/Library/Frameworks/CoreServices.framework/CoreServices')
        create = framework.AECreateDesc
        create.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int32, ctypes.POINTER(_AEDesc)]
        create.restype = ctypes.c_int16
        dispose = framework.AEDisposeDesc
        dispose.argtypes = [ctypes.POINTER(_AEDesc)]
        dispose.restype = ctypes.c_int16
        permission = framework.AEDeterminePermissionToAutomateTarget
        permission.argtypes = [ctypes.POINTER(_AEDesc), ctypes.c_uint32, ctypes.c_uint32, ctypes.c_bool]
        permission.restype = ctypes.c_int32
    except (OSError, AttributeError) as exc:
        raise WorkError('macOS could not check Terminal Automation permission') from exc

    target = _AEDesc()
    bundle = b'com.apple.Terminal'
    result = create(int.from_bytes(b'bund', 'big'), bundle, len(bundle), ctypes.byref(target))
    if result:
        raise WorkError(f'macOS could not identify Terminal for its permission check ({result})')
    try:
        return permission(ctypes.byref(target), int.from_bytes(b'core', 'big'),
                          int.from_bytes(b'dosc', 'big'), ask_user)
    finally:
        dispose(ctypes.byref(target))
