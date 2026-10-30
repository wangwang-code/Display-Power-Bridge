from PyInstaller.utils.hooks.tcl_tk import tcltk_info


def _keep_data(destination):
    path = destination.replace("\\", "/")
    if path.startswith("_tcl_data/tzdata/"):
        return False
    if path.startswith("_tcl_data/opt0.4/"):
        return False
    if path.startswith("_tcl_data/http1.0/"):
        return False
    if path.startswith("_tcl_data/msgs/"):
        return path.endswith(("/en.msg", "/zh.msg", "/zh_cn.msg"))
    if path.startswith("_tcl_data/encoding/"):
        return path.endswith((
            "/ascii.enc", "/cp1252.enc", "/cp936.enc", "/cp932.enc",
            "/cp949.enc", "/cp950.enc", "/gb2312.enc", "/iso8859-1.enc",
        ))
    if path.startswith("tcl8/8.4/platform"):
        return False
    if path in ("tcl8/8.6/http-2.9.8.tm", "_tk_data/tkfbox.tcl"):
        return False
    if path.endswith("/tcltest-2.5.8.tm"):
        return False
    if path.startswith("_tk_data/images/"):
        return False
    if path == "_tk_data/license.terms":
        return False
    if path.startswith("_tk_data/") and path.endswith((
        "/bgerror.tcl", "/choosedir.tcl", "/clrpick.tcl", "/comdlg.tcl",
        "/console.tcl", "/fontchooser.tcl", "/iconlist.tcl", "/mkpsenc.tcl",
        "/obsolete.tcl", "/palette.tcl", "/safetk.tcl", "/tearoff.tcl",
        "/unsupported.tcl", "/xmfbox.tcl",
    )):
        return False
    return True


def hook(hook_api):
    if tcltk_info.tcl_data_missing:
        raise SystemExit(f"ERROR: Tcl data/library directory ({tcltk_info.tcl_data_dir!r}) could not be collected!")
    if tcltk_info.tk_data_missing:
        raise SystemExit(f"ERROR: Tk data/library directory ({tcltk_info.tk_data_dir!r}) could not be collected!")
    hook_api.add_datas([item for item in tcltk_info.data_files if _keep_data(item[0])])
