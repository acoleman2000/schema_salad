"""Python code generator for a given schema salad definition."""
import textwrap
from io import StringIO
from typing import (
    IO,
    Any,
    Dict,
    List,
    MutableMapping,
    MutableSequence,
    Optional,
    Set,
    Union,
)

try:
    import black
except ModuleNotFoundError:
    black = None  # type: ignore[assignment]

from pkg_resources import resource_stream

from . import schema
from .codegen_base import CodeGenBase, TypeDef
from .exceptions import SchemaException
from .schema import shortname

_string_type_def = TypeDef("strtype", "_PrimitiveLoader(str)")
_int_type_def = TypeDef("inttype", "_PrimitiveLoader(int)")
_float_type_def = TypeDef("floattype", "_PrimitiveLoader(float)")
_bool_type_def = TypeDef("booltype", "_PrimitiveLoader(bool)")
_null_type_def = TypeDef("None_type", "_PrimitiveLoader(type(None))")
_any_type_def = TypeDef("Any_type", "_AnyLoader()")

prims = {
    "http://www.w3.org/2001/XMLSchema#string": _string_type_def,
    "http://www.w3.org/2001/XMLSchema#int": _int_type_def,
    "http://www.w3.org/2001/XMLSchema#long": _int_type_def,
    "http://www.w3.org/2001/XMLSchema#float": _float_type_def,
    "http://www.w3.org/2001/XMLSchema#double": _float_type_def,
    "http://www.w3.org/2001/XMLSchema#boolean": _bool_type_def,
    "https://w3id.org/cwl/salad#null": _null_type_def,
    "https://w3id.org/cwl/salad#Any": _any_type_def,
    "string": _string_type_def,
    "int": _int_type_def,
    "long": _int_type_def,
    "float": _float_type_def,
    "double": _float_type_def,
    "boolean": _bool_type_def,
    "null": _null_type_def,
    "Any": _any_type_def,
}


def fmt(text: str, indent: int) -> str:
    """
    Use black to format this snippet.

    :param indent the indent level for the current context
    """
    if not black:
        raise Exception(
            "Must install 'black' to use the Python code generator. "
            "If installing schema-salad via pip, try `pip install schema-salad[pycodegen]`."
        )
    return textwrap.indent(
        black.format_str(
            text,
            mode=black.mode.Mode(
                target_versions={black.mode.TargetVersion.PY36}, line_length=88 - indent
            ),
        ),
        " " * indent,
    )


class PythonCodeGen(CodeGenBase):
    """Generation of Python code for a given Schema Salad definition."""

    def __init__(
        self,
        out: IO[str],
        copyright: Optional[str],
        parser_info: str,
    ) -> None:
        super().__init__()
        self.out = out
        self.current_class_is_abstract = False
        self.serializer = StringIO()
        self.idfield = ""
        self.copyright = copyright
        self.parser_info = parser_info

    @staticmethod
    def safe_name(name: str) -> str:
        """Generate a safe version of the given name."""
        avn = schema.avro_field_name(name)
        if avn.startswith("anon."):
            avn = avn[5:]
        elif avn[0].isdigit():
            avn = f"_{avn}"
        elif avn in ("class", "in", "type"):
            # reserved words
            avn = f"{avn}_"
        return avn.replace(".", "_")

    def prologue(self) -> None:
        """Trigger to generate the prolouge code."""
        self.out.write(
            """#
# This file was autogenerated using schema-salad-tool --codegen=python
# The code itself is released under the Apache 2.0 license and the help text is
# subject to the license of the original schema.
"""
        )
        if self.copyright:
            self.out.write(
                """
#
# The original schema is {copyright}.
""".format(
                    copyright=self.copyright
                )
            )

        stream = resource_stream(__name__, "python_codegen_support.py")
        python_codegen_support = stream.read().decode("UTF-8")
        self.out.write(python_codegen_support[python_codegen_support.find("\n") + 1 :])
        stream.close()
        self.out.write("\n\n")

        self.out.write(
            f"""def parser_info() -> str:
    return "{self.parser_info}"


"""  # noqa: B907
        )

        for primitive in prims.values():
            self.declare_type(primitive)

    def begin_class(
        self,  # pylint: disable=too-many-arguments
        classname: str,
        extends: MutableSequence[str],
        doc: str,
        abstract: bool,
        field_names: MutableSequence[str],
        idfield: str,
        optional_fields: Set[str],
    ) -> None:
        classname = self.safe_name(classname)

        if extends:
            ext = ", ".join(self.safe_name(e) for e in extends)
        else:
            ext = "Saveable"

        self.out.write(fmt(f"class {classname}({ext}):\n    pass", 0)[:-9])
        # make a valid class for Black, but then trim off the "pass"

        if doc:
            self.out.write(fmt(f'"""\n{doc}\n"""\n', 4) + "\n")

        self.serializer = StringIO()

        self.current_class_is_abstract = abstract
        if self.current_class_is_abstract:
            self.out.write("    pass\n\n\n")
            return

        required_field_names = [f for f in field_names if f not in optional_fields]
        optional_field_names = [f for f in field_names if f in optional_fields]

        safe_inits: List[str] = ["        self,"]
        safe_inits.extend(
            [f"        {self.safe_name(f)}: Any," for f in required_field_names if f != "class"]
        )
        safe_inits.extend(
            [
                f"        {self.safe_name(f)}: Optional[Any] = None,"
                for f in optional_field_names
                if f != "class"
            ]
        )
        self.out.write(
            "    def __init__(\n"
            + "\n".join(safe_inits)
            + "\n        extension_fields: Optional[Dict[str, Any]] = None,"
            + "\n        loadingOptions: Optional[LoadingOptions] = None,"
            + "\n    ) -> None:\n"
            + """        if extension_fields:
            self.extension_fields = extension_fields
        else:
            self.extension_fields = CommentedMap()
        if loadingOptions:
            self.loadingOptions = loadingOptions
        else:
            self.loadingOptions = LoadingOptions()
"""
        )
        field_inits = ""
        for name in field_names:
            if name == "class":
                field_inits += """        self.class_ = "{}"
""".format(
                    classname
                )
            else:
                field_inits += """        self.{0} = {0}
""".format(
                    self.safe_name(name)
                )
        field_eqs = []
        field_hashes = []
        for name in field_names:
            field_eqs.append("self.{0} == other.{0}".format(self.safe_name(name)))
            field_hashes.append(f"self.{self.safe_name(name)}")
        field_eq = " and\n                    ".join(field_eqs)
        field_hash = ",\n            ".join(field_hashes)
        self.out.write(field_inits)
        self.out.write(
            "\n"
            + fmt(
                f"""def __eq__(
    self,
    other: Any
) -> bool:
    if isinstance(other, {classname}):
        return bool({field_eq})
    return False
""",
                4,
            )
        )
        self.out.write(
            "\n"
            + fmt(
                f"""def __hash__(self) -> int:
    return hash((
        {field_hash}
    ))
""",
                4,
            )
        )

        self.out.write(
            f"""
    @classmethod
    def fromDoc(
        cls,
        doc: Any,
        baseuri: str,
        loadingOptions: LoadingOptions,
        docRoot: Optional[str] = None
    ) -> "{classname}":
        _doc = copy.copy(doc)

        if hasattr(doc, "lc"):
            _doc.lc.data = doc.lc.data
            _doc.lc.filename = doc.lc.filename
        _errors__ = []
"""  # noqa: B907
        )

        self.idfield = idfield

        self.serializer.write(
            """
    def save(
        self, top: bool = False, base_url: str = "", relative_uris: bool = True
    ) -> Dict[str, Any]:
        r: Dict[str, Any] = {}

        if relative_uris:
            for ef in self.extension_fields:
                r[prefix_url(ef, self.loadingOptions.vocab)] = self.extension_fields[ef]
        else:
            for ef in self.extension_fields:
                r[ef] = self.extension_fields[ef]
"""
        )

        if "class" in field_names:
            self.out.write(
                """
        if "class" not in _doc:
            raise ValidationException("Missing 'class' field")
        if _doc.get("class") != "{class_}":
            raise ValidationException("tried `{class_}` but")

""".format(
                    class_=classname
                )
            )

            self.serializer.write(
                """
        r["class"] = "{class_}"
""".format(
                    class_=classname
                )
            )

    def end_class(self, classname: str, field_names: List[str]) -> None:
        """Signal that we are done with this class."""
        if self.current_class_is_abstract:
            return

        self.out.write(
            fmt(
                """
extension_fields: Dict[str, Any] = {{}}
for k in _doc.keys():
    if k not in cls.attrs:
        if not k:
            _errors__.append(
                ValidationException("mapping with implicit null key")
            )
        elif ":" in k:
            ex = expand_url(
                k, "", loadingOptions, scoped_id=False, vocab_term=False
            )
            extension_fields[ex] = _doc[k]
        else:
            _errors__.append(
                ValidationException(
                    "invalid field `{{}}`, expected one of: {attrstr}".format(
                        k
                    ),
                    SourceLine(_doc, k, str),
                )
            )

if _errors__:
    raise ValidationException("", None, _errors__, "*")
""".format(
                    attrstr=", ".join([f"`{f}`" for f in field_names]),
                ),
                8,
            )
        )

        self.serializer.write(
            """
        # top refers to the directory level
        if top:
            if self.loadingOptions.namespaces:
                r["$namespaces"] = self.loadingOptions.namespaces
            if self.loadingOptions.schemas:
                r["$schemas"] = self.loadingOptions.schemas
"""
        )

        self.serializer.write("        return r\n\n")

        self.serializer.write(
            fmt(
                f"""attrs = frozenset(["{'", "'.join(field_names)}"])\n""",  # noqa: B907
                4,
            )
        )

        safe_init_fields = [
            self.safe_name(f) for f in field_names if f != "class"
        ]  # type: List[str]

        safe_inits = [f + "=" + f for f in safe_init_fields]

        safe_inits.extend(["extension_fields=extension_fields", "loadingOptions=loadingOptions"])

        self.out.write(
            "        _constructed = cls(\n            "
            + ",\n            ".join(safe_inits)
            + ",\n        )\n"
        )
        if self.idfield:
            self.out.write(
                f"        loadingOptions.idx[{self.safe_name(self.idfield)}] "
                "= (_constructed, loadingOptions)\n"
            )

        self.out.write("        return _constructed\n")

        self.out.write(str(self.serializer.getvalue()))

        self.out.write("\n\n")

    def type_loader(self, type_declaration: Union[List[Any], Dict[str, Any], str]) -> TypeDef:
        """Parse the given type declaration and declare its components."""
        if isinstance(type_declaration, MutableSequence):
            sub_names: List[str] = list(
                dict.fromkeys([self.type_loader(i).name for i in type_declaration])
            )
            return self.declare_type(
                TypeDef(
                    "union_of_{}".format("_or_".join(sub_names)),
                    "_UnionLoader(({},))".format(", ".join(sub_names)),
                )
            )
        if isinstance(type_declaration, MutableMapping):
            if type_declaration["type"] in (
                "array",
                "https://w3id.org/cwl/salad#array",
            ):
                i = self.type_loader(type_declaration["items"])
                return self.declare_type(TypeDef(f"array_of_{i.name}", f"_ArrayLoader({i.name})"))
            if type_declaration["type"] in ("enum", "https://w3id.org/cwl/salad#enum"):
                for sym in type_declaration["symbols"]:
                    self.add_vocab(shortname(sym), sym)
                if "doc" in type_declaration:
                    doc = type_declaration["doc"]
                    if isinstance(doc, MutableSequence):
                        formated_doc = "\n".join(doc)
                    else:
                        formated_doc = doc.strip()
                    docstring = f'\n"""\n{formated_doc}\n"""'
                else:
                    docstring = ""
                return self.declare_type(
                    TypeDef(
                        self.safe_name(type_declaration["name"]) + "Loader",
                        '_EnumLoader(("{}",), "{}"){}'.format(
                            '", "'.join(
                                schema.avro_field_name(sym) for sym in type_declaration["symbols"]
                            ),
                            self.safe_name(type_declaration["name"]),
                            docstring,
                        ),
                    )
                )

            if type_declaration["type"] in (
                "record",
                "https://w3id.org/cwl/salad#record",
            ):
                return self.declare_type(
                    TypeDef(
                        self.safe_name(type_declaration["name"]) + "Loader",
                        "_RecordLoader({})".format(
                            self.safe_name(type_declaration["name"]),
                        ),
                        abstract=type_declaration.get("abstract", False),
                    )
                )
            raise SchemaException("wft {}".format(type_declaration["type"]))

        if type_declaration in prims:
            return prims[type_declaration]

        if type_declaration in ("Expression", "https://w3id.org/cwl/cwl#Expression"):
            return self.declare_type(
                TypeDef(
                    self.safe_name(type_declaration) + "Loader",
                    "_ExpressionLoader(str)",
                )
            )
        return self.collected_types[self.safe_name(type_declaration) + "Loader"]

    def declare_id_field(
        self,
        name: str,
        fieldtype: TypeDef,
        doc: str,
        optional: bool,
    ) -> None:
        if self.current_class_is_abstract:
            return

        self.declare_field(name, fieldtype, doc, True, "")

        if optional:
            opt = """{safename} = "_:" + str(_uuid__.uuid4())""".format(
                safename=self.safe_name(name)
            )
        else:
            opt = """_errors__.append(ValidationException("missing {fieldname}"))""".format(
                fieldname=shortname(name)
            )

        self.out.write(
            """
        __original_{safename}_is_none = {safename} is None
        if {safename} is None:
            if docRoot is not None:
                {safename} = docRoot
            else:
                {opt}
        if not __original_{safename}_is_none:
            baseuri = {safename}
""".format(
                safename=self.safe_name(name), opt=opt
            )
        )

    def declare_field(
        self,
        name: str,
        fieldtype: TypeDef,
        doc: Optional[str],
        optional: bool,
        subscope: str,
    ) -> None:
        if self.current_class_is_abstract:
            return

        if shortname(name) == "class":
            return

        if optional:
            self.out.write(f"""        if "{shortname(name)}" in _doc:\n""")  # noqa: B907
            spc = "    "
        else:
            spc = ""

        if subscope:
            self.out.write(
                """
{spc}        subscope_baseuri = expand_url('{subscope}', baseuri, loadingOptions, True)
""".format(
                    subscope=subscope, spc=spc
                )
            )
            baseurivar = "subscope_baseuri"
        else:
            baseurivar = "baseuri"

        self.out.write(
            """{spc}        try:
{spc}            if _doc.get("{fieldname}") is None:
{spc}                raise ValidationException("missing required field `{fieldname}`", None, [])

{spc}            {safename} = load_field(
{spc}                _doc.get("{fieldname}"),
{spc}                {fieldtype},
{spc}                {baseurivar},
{spc}                loadingOptions,
{spc}                lc=_doc.get("{fieldname}")
{spc}            )
""".format(
                safename=self.safe_name(name),
                fieldname=shortname(name),
                fieldtype=fieldtype.name,
                baseurivar=baseurivar,
                spc=spc,
            )
        )
        self.out.write(
            """
{spc}        except ValidationException as e:
{spc}            error_message = parse_errors(str(e))

{spc}            if str(e) == "missing required field `{fieldname}`":
{spc}                _errors__.append(
{spc}                    ValidationException(
{spc}                        str(e),
{spc}                        None
{spc}                    )
{spc}                )
{spc}            else:
{spc}                if error_message != str(e):
{spc}                    val_type = convert_typing(extract_type(type(_doc.get("{fieldname}"))))
{spc}                    _errors__.append(
{spc}                        ValidationException(
{spc}                            \"the `{fieldname}` field is not valid because:\",
{spc}                            SourceLine(_doc, "{fieldname}", str),
{spc}                            [ValidationException(f"Expected one of {{error_message}} was {{val_type}}")],
{spc}                        )
{spc}                    )
{spc}                else:
{spc}                    _errors__.append(
{spc}                        ValidationException(
{spc}                            \"the `{fieldname}` field is not valid because:\",
{spc}                            SourceLine(_doc, "{fieldname}", str),
{spc}                            [e],
{spc}                        )
{spc}                    )
""".format(
                fieldname=shortname(name),
                spc=spc,
            )
        )
        if optional:
            self.out.write(
                """        else:
            {safename} = None
""".format(
                    safename=self.safe_name(name)
                )
            )

        if name == self.idfield or not self.idfield:
            baseurl = "base_url"
        else:
            baseurl = f"self.{self.safe_name(self.idfield)}"

        if fieldtype.is_uri:
            self.serializer.write(
                fmt(
                    """
if self.{safename} is not None:
    u = save_relative_uri(self.{safename}, {baseurl}, {scoped_id}, {ref_scope}, relative_uris)
    r["{fieldname}"] = u
""".format(
                        safename=self.safe_name(name),
                        fieldname=shortname(name).strip(),
                        baseurl=baseurl,
                        scoped_id=fieldtype.scoped_id,
                        ref_scope=fieldtype.ref_scope,
                    ),
                    8,
                )
            )
        else:
            self.serializer.write(
                fmt(
                    """
if self.{safename} is not None:
    r["{fieldname}"] = save(
        self.{safename}, top=False, base_url={baseurl}, relative_uris=relative_uris
    )
""".format(
                        safename=self.safe_name(name),
                        fieldname=shortname(name),
                        baseurl=baseurl,
                    ),
                    8,
                )
            )

    def uri_loader(
        self,
        inner: TypeDef,
        scoped_id: bool,
        vocab_term: bool,
        ref_scope: Optional[int],
    ) -> TypeDef:
        """Construct the TypeDef for the given URI loader."""
        return self.declare_type(
            TypeDef(
                f"uri_{inner.name}_{scoped_id}_{vocab_term}_{ref_scope}",
                f"_URILoader({inner.name}, {scoped_id}, {vocab_term}, {ref_scope})",
                is_uri=True,
                scoped_id=scoped_id,
                ref_scope=ref_scope,
            )
        )

    def idmap_loader(
        self, field: str, inner: TypeDef, map_subject: str, map_predicate: Optional[str]
    ) -> TypeDef:
        """Construct the TypeDef for the given mapped ID loader."""
        return self.declare_type(
            TypeDef(
                f"idmap_{self.safe_name(field)}_{inner.name}",
                f"_IdMapLoader({inner.name}, '{map_subject}', '{map_predicate}')",  # noqa: B907
            )
        )

    def typedsl_loader(self, inner: TypeDef, ref_scope: Optional[int]) -> TypeDef:
        """Construct the TypeDef for the given DSL loader."""
        return self.declare_type(
            TypeDef(
                f"typedsl_{self.safe_name(inner.name)}_{ref_scope}",
                f"_TypeDSLLoader({self.safe_name(inner.name)}, {ref_scope})",
            )
        )

    def secondaryfilesdsl_loader(self, inner: TypeDef) -> TypeDef:
        """Construct the TypeDef for secondary files."""
        return self.declare_type(
            TypeDef(
                f"secondaryfilesdsl_{inner.name}",
                f"_SecondaryDSLLoader({inner.name})",
            )
        )

    def epilogue(self, root_loader: TypeDef) -> None:
        """Trigger to generate the epilouge code."""
        self.out.write("_vocab = {\n")
        for k in sorted(self.vocab.keys()):
            self.out.write(f'    "{k}": "{self.vocab[k]}",\n')  # noqa: B907
        self.out.write("}\n")

        self.out.write("_rvocab = {\n")
        for k in sorted(self.vocab.keys()):
            self.out.write(f'    "{self.vocab[k]}": "{k}",\n')  # noqa: B907
        self.out.write("}\n\n")

        for _, collected_type in self.collected_types.items():
            if not collected_type.abstract:
                self.out.write(fmt(f"{collected_type.name} = {collected_type.init}\n", 0))
        self.out.write("\n")

        self.out.write(
            """
def load_document(
    doc: Any,
    baseuri: Optional[str] = None,
    loadingOptions: Optional[LoadingOptions] = None,
) -> Any:
    if baseuri is None:
        baseuri = file_uri(os.getcwd()) + "/"
    if loadingOptions is None:
        loadingOptions = LoadingOptions()
    result, metadata = _document_load(
        %(name)s,
        doc,
        baseuri,
        loadingOptions,
    )
    return result


def load_document_with_metadata(
    doc: Any,
    baseuri: Optional[str] = None,
    loadingOptions: Optional[LoadingOptions] = None,
    addl_metadata_fields: Optional[MutableSequence[str]] = None,
) -> Any:
    if baseuri is None:
        baseuri = file_uri(os.getcwd()) + "/"
    if loadingOptions is None:
        loadingOptions = LoadingOptions(fileuri=baseuri)
    return _document_load(
        %(name)s,
        doc,
        baseuri,
        loadingOptions,
        addl_metadata_fields=addl_metadata_fields,
    )


def load_document_by_string(
    string: Any,
    uri: str,
    loadingOptions: Optional[LoadingOptions] = None,
) -> Any:
    yaml = yaml_no_ts()
    result = yaml.load(string)
    add_lc_filename(result, uri)

    if loadingOptions is None:
        loadingOptions = LoadingOptions(fileuri=uri)

    result, metadata = _document_load(
        %(name)s,
        result,
        uri,
        loadingOptions,
    )
    return result


def load_document_by_yaml(
    yaml: Any,
    uri: str,
    loadingOptions: Optional[LoadingOptions] = None,
) -> Any:
    """
            '"""'
            """
    Shortcut to load via a YAML object.
    yaml: must be from ruamel.yaml.main.YAML.load with preserve_quotes=True
    """
            '"""'
            """
    add_lc_filename(yaml, uri)

    if loadingOptions is None:
        loadingOptions = LoadingOptions(fileuri=uri)

    result, metadata = _document_load(
        %(name)s,
        yaml,
        uri,
        loadingOptions,
    )
    return result
"""
            % dict(name=root_loader.name)
        )
