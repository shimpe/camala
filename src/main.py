from captiongenerator import CaptionGenerator
import ttkbootstrap as ttkb
from ttkbootstrap.constants import *
from ttkbootstrap.dialogs import Messagebox
from tkinter.filedialog import askopenfilename, askdirectory 
import pathlib
import contextlib


class StdoutRedirector: # https://gist.github.com/kylenahas/a07f2ce8ced689975eae56d6eaad770f
    def __init__(self, parent, text_widget):
        self.text_space = text_widget
        self.parent = parent

    def write(self,string):
        self.text_space.insert('end', string)
        self.text_space.see('end')
        self.parent.update_idletasks()

class Gui(ttkb.Frame):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pack(fill=BOTH, expand=YES)

        browse_frm = ttkb.Frame(self)
        browse_frm.rowconfigure(tuple(range(3)), weight=1, minsize=10)
        browse_frm.rowconfigure((4,), weight=100, minsize=10)
        browse_frm.columnconfigure((0,2), weight=1, minsize=10)
        browse_frm.columnconfigure((1,), weight=5, minsize=10)
        browse_frm.pack(side=TOP, fill=X, padx=5, pady=5)

        # path to inkscape
        path_to_inkscape_label = ttkb.Label(browse_frm, text="Path to inkscape: ")
        path_to_inkscape_label.grid(column=0, row=0, padx=10, pady=10, ipadx=5, ipady=5, sticky="w")

        path_to_inkscape_entry = ttkb.Entry(browse_frm, textvariable='inkscape-path')
        path_to_inkscape_entry.grid(column=1, row=0, padx=10, pady=10, ipadx=5, ipady=5, sticky="news")
        c = CaptionGenerator("")
        self.setvar("inkscape-path", c.inkscape)

        browse_inkscape_button = ttkb.Button(
            master=browse_frm,
            text="Browse",
            bootstyle=(OUTLINE, SECONDARY),
            command=self.get_path_to_inkscape
        )
        browse_inkscape_button.grid(column=2, row=0, padx=10, pady=10, ipadx=5, ipady=5)

        # output folder
        output_folder_label = ttkb.Label(browse_frm, text="Path to result folder: ")
        output_folder_label.grid(column=0, row=1, padx=10, pady=10, ipadx=5, ipady=5, sticky="w")

        output_folder_entry = ttkb.Entry(browse_frm, textvariable='output-folder')
        output_folder_entry.grid(column=1, row=1, padx=10, pady=10, ipadx=5, ipady=5, sticky="news")
        self.setvar('output-folder', str(pathlib.Path(".").absolute()))

        output_folder_button = ttkb.Button(
            master=browse_frm,
            text="Browse",
            bootstyle=(OUTLINE, SECONDARY),
            command=self.get_path_to_output_folder
        )
        output_folder_button.grid(column=2, row=1, padx=10, pady=10, ipadx=5, ipady=5)
        
        # toml file
        toml_file_label = ttkb.Label(browse_frm, text="Path to toml file: ")
        toml_file_label.grid(column=0, row=2, padx=10, pady=10, ipadx=5, ipady=5, sticky="w")

        toml_file_entry = ttkb.Entry(browse_frm, textvariable='toml-file')
        toml_file_entry.grid(column=1, row=2, padx=10, pady=10, ipadx=5, ipady=5, sticky="news")
        self.setvar('toml-file', "")

        toml_file_button = ttkb.Button(
            master=browse_frm,
            text="Browse",
            bootstyle=(OUTLINE, SECONDARY),
            command=self.get_path_to_toml_file
        )
        toml_file_button.grid(column=2, row=2, padx=10, pady=10, ipadx=5, ipady=5)

        # generate button
        generate_btn = ttkb.Button(master=browse_frm, text="Generate", command=self.generate)
        generate_btn.grid(column=0, columnspan=3, padx=10, pady=10, ipadx=5, ipady=5)

        self.terminal_output = ttkb.ScrolledText(master=browse_frm)
        self.terminal_output.grid(column=0, columnspan=3, row=4, padx=10, pady=10, ipadx=5, ipady=5, sticky="news")


    def get_path_to_inkscape(self):
        self.update_idletasks()
        f = askopenfilename(se)
        if f:
            self.setvar('inkscape-path', f)

    def get_path_to_toml_file(self):
        self.update_idletasks()
        f = askopenfilename()
        if f:
            self.setvar('toml-file', f)

    def get_path_to_output_folder(self):
        self.update_idletasks()
        d = askdirectory()
        if d:
            self.setvar('output-folder', d)

    def generate(self):
        inkscape = pathlib.Path(self.getvar('inkscape-path'))
        if not inkscape.is_file():
            Messagebox.ok(message=f"{inkscape} is not a file.\nPlease select a valid path to inkscape first.")
            return
        toml = pathlib.Path(self.getvar('toml-file'))
        if not toml.is_file():
            Messagebox.ok(message=f"{toml} is not a file.\nPlease select a valid path to a .toml file first.")
            return

        output_folder = pathlib.Path(self.getvar('output-folder'))
        if not output_folder.is_dir():
            Messagebox.ok(message=f"{output_folder} is not an existing folder.\nPlease select an existing result folder first.")
            return

        output_file = output_folder.joinpath(toml.stem)

        try:
            with contextlib.redirect_stdout(StdoutRedirector(self, self.terminal_output)),\
                    contextlib.redirect_stderr(StdoutRedirector(self, self.terminal_output)):
                c = CaptionGenerator(str(output_file))
                c.write_videofile(input=str(toml))
        except Exception as e:
            Messagebox.ok(message=f"An exception occurred while processing your file.\n{e}")

def main():
    app = ttkb.Window("Caption Generator", themename="darkly")
    Gui(app)
    app.mainloop()

if __name__ == "__main__":
    main()