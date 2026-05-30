const Applet = imports.ui.applet;
const GLib = imports.gi.GLib;
const PopupMenu = imports.ui.popupMenu;

const TELLYKEYS = "/home/robert/Dokumenter/Kode-prosjekter/tellykeys/.venv/bin/tellykeys";

class TellyKeysApplet extends Applet.IconApplet {
    constructor(orientation, panel_height, instance_id) {
        super(orientation, panel_height, instance_id);

        this.set_applet_icon_name("tellykeys");
        this.set_applet_tooltip("TellyKeys");

        this.menuManager = new PopupMenu.PopupMenuManager(this);
        this.menu = new Applet.AppletPopupMenu(this, orientation);
        this.menuManager.addMenu(this.menu);

        this._addItem("Open TellyKeys", () => this._run(`${TELLYKEYS}`));
        this._addItem("Start tray mode", () => this._run(`${TELLYKEYS} --start-hidden`));
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._addItem("Quit TellyKeys", () => this._run("pkill -f tellykeys"));
    }

    _addItem(label, callback) {
        let item = new PopupMenu.PopupMenuItem(label);
        item.connect("activate", callback);
        this.menu.addMenuItem(item);
    }

    _run(command) {
        try {
            GLib.spawn_command_line_async(command);
        } catch (e) {
            global.logError(e);
        }
    }

    on_applet_clicked() {
        this.menu.toggle();
    }
}

function main(metadata, orientation, panel_height, instance_id) {
    return new TellyKeysApplet(orientation, panel_height, instance_id);
}
