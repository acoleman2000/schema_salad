"""Template code used by python_codegen.py."""
import copy
import logging
import os
import pathlib
import re
import tempfile
import uuid as _uuid__  # pylint: disable=unused-import # noqa: F401
import xml.sax  # nosec
from abc import ABC, abstractmethod
from io import StringIO
from typing import (
    Any,
    Dict,
    List,
    MutableMapping,
    MutableSequence,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
    cast,
)
from urllib.parse import quote, urldefrag, urlparse, urlsplit, urlunsplit
from urllib.request import pathname2url, url2pathname

from rdflib import Graph
from rdflib.plugins.parsers.notation3 import BadSyntax
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from schema_salad.exceptions import SchemaSaladException, ValidationException
from schema_salad.fetcher import DefaultFetcher, Fetcher, MemoryCachingFetcher
from schema_salad.sourceline import SourceLine, add_lc_filename
from schema_salad.utils import CacheType, yaml_no_ts  # requires schema-salad v8.2+

_vocab: Dict[str, str] = {}
_rvocab: Dict[str, str] = {}

_logger = logging.getLogger("salad")


IdxType = MutableMapping[str, Tuple[Any, "LoadingOptions"]]

line_numbers = CommentedMap()


class LoadingOptions:
    idx: IdxType
    fileuri: Optional[str]
    baseuri: str
    namespaces: MutableMapping[str, str]
    schemas: MutableSequence[str]
    original_doc: Optional[Any]
    addl_metadata: MutableMapping[str, Any]
    fetcher: Fetcher
    vocab: Dict[str, str]
    rvocab: Dict[str, str]
    cache: CacheType
    imports: List[str]
    includes: List[str]

    def __init__(
        self,
        fetcher: Optional[Fetcher] = None,
        namespaces: Optional[Dict[str, str]] = None,
        schemas: Optional[List[str]] = None,
        fileuri: Optional[str] = None,
        copyfrom: Optional["LoadingOptions"] = None,
        original_doc: Optional[Any] = None,
        addl_metadata: Optional[Dict[str, str]] = None,
        baseuri: Optional[str] = None,
        idx: Optional[IdxType] = None,
        imports: Optional[List[str]] = None,
        includes: Optional[List[str]] = None,
    ) -> None:
        """Create a LoadingOptions object."""
        self.original_doc = original_doc

        if idx is not None:
            self.idx = idx
        else:
            self.idx = copyfrom.idx if copyfrom is not None else {}

        if fileuri is not None:
            self.fileuri = fileuri
        else:
            self.fileuri = copyfrom.fileuri if copyfrom is not None else None

        if baseuri is not None:
            self.baseuri = baseuri
        else:
            self.baseuri = copyfrom.baseuri if copyfrom is not None else ""

        if namespaces is not None:
            self.namespaces = namespaces
        else:
            self.namespaces = copyfrom.namespaces if copyfrom is not None else {}

        if schemas is not None:
            self.schemas = schemas
        else:
            self.schemas = copyfrom.schemas if copyfrom is not None else []

        if addl_metadata is not None:
            self.addl_metadata = addl_metadata
        else:
            self.addl_metadata = copyfrom.addl_metadata if copyfrom is not None else {}

        if imports is not None:
            self.imports = imports
        else:
            self.imports = copyfrom.imports if copyfrom is not None else []

        if includes is not None:
            self.includes = includes
        else:
            self.includes = copyfrom.includes if copyfrom is not None else []

        if fetcher is not None:
            self.fetcher = fetcher
        elif copyfrom is not None:
            self.fetcher = copyfrom.fetcher
        else:
            import requests
            from cachecontrol.caches import FileCache
            from cachecontrol.wrapper import CacheControl

            root = pathlib.Path(os.environ.get("HOME", tempfile.gettempdir()))
            session = CacheControl(
                requests.Session(),
                cache=FileCache(root / ".cache" / "salad"),
            )
            self.fetcher: Fetcher = DefaultFetcher({}, session)

        self.cache = self.fetcher.cache if isinstance(self.fetcher, MemoryCachingFetcher) else {}

        self.vocab = _vocab
        self.rvocab = _rvocab

        if namespaces is not None:
            self.vocab = self.vocab.copy()
            self.rvocab = self.rvocab.copy()
            for k, v in namespaces.items():
                self.vocab[k] = v
                self.rvocab[v] = k

    def check_exists(self, url: str) -> bool:
        """Check if a url exists and is valid."""
        if url in self.cache:
            return True

        split = urlsplit(url)
        scheme, path = split.scheme, split.path

        if scheme in ["http", "https"]:
            if self.fetcher is None:
                raise ValidationException(f"Can't check {self.fetcher} URL, fetcher is None")
            try:
                self.fetcher.fetch_text(url)
            except Exception:
                return False
            self.cache[url] = True
            return True
        if scheme == "file":
            return os.path.exists(url2pathname(str(path)))
        if scheme == "mailto":
            return True
        raise ValidationException(f"Unsupported scheme '{scheme}' in url: {url}")

    @property
    def graph(self) -> Graph:
        """Generate a merged rdflib.Graph from all entries in self.schemas."""
        graph = Graph()
        if not self.schemas:
            return graph
        key = str(hash(tuple(self.schemas)))
        if key in self.cache:
            return cast(Graph, self.cache[key])
        for schema in self.schemas:
            fetchurl = (
                self.fetcher.urljoin(self.fileuri, schema)
                if self.fileuri is not None
                else pathlib.Path(schema).resolve().as_uri()
            )
            if fetchurl not in self.cache or self.cache[fetchurl] is True:
                _logger.debug("Getting external schema %s", fetchurl)
                try:
                    content = self.fetcher.fetch_text(fetchurl)
                except Exception as e:
                    _logger.warning("Could not load extension schema %s: %s", fetchurl, str(e))
                    continue
                newGraph = Graph()
                err_msg = "unknown error"
                for fmt in ["xml", "turtle"]:
                    try:
                        newGraph.parse(data=content, format=fmt, publicID=str(fetchurl))
                        self.cache[fetchurl] = newGraph
                        graph += newGraph
                        break
                    except (xml.sax.SAXParseException, TypeError, BadSyntax) as e:
                        err_msg = str(e)
                else:
                    _logger.warning("Could not load extension schema %s: %s", fetchurl, err_msg)
        self.cache[key] = graph
        return graph


class Saveable(ABC):
    """Mark classes than have a save() and fromDoc() function."""

    @classmethod
    @abstractmethod
    def fromDoc(
        cls,
        _doc: Any,
        baseuri: str,
        loadingOptions: LoadingOptions,
        keys: Optional[List[str]] = None,
        docRoot: Optional[str] = None,
    ) -> "Saveable":
        """Construct this object from the result of yaml.load()."""

    @abstractmethod
    def save(
        self, top: bool = False, base_url: str = "", relative_uris: bool = True
    ) -> Dict[str, Any]:
        """Convert this object to a JSON/YAML friendly dictionary."""


def load_field(val, fieldtype, baseuri, loadingOptions, keys=None):
    """Load field."""
    # type: (Union[str, Dict[str, str]], _Loader, str, LoadingOptions,Optional[List[str]]) -> Any
    if isinstance(val, MutableMapping):
        if "$import" in val:
            if loadingOptions.fileuri is None:
                raise SchemaSaladException("Cannot load $import without fileuri")
            url = loadingOptions.fetcher.urljoin(loadingOptions.fileuri, val["$import"])
            result, metadata = _document_load_by_url(
                fieldtype,
                url,
                loadingOptions,
            )
            loadingOptions.imports.append(url)
            return result
        elif "$include" in val:
            if loadingOptions.fileuri is None:
                raise SchemaSaladException("Cannot load $import without fileuri")
            url = loadingOptions.fetcher.urljoin(loadingOptions.fileuri, val["$include"])
            val = loadingOptions.fetcher.fetch_text(url)
            loadingOptions.includes.append(url)
    return fieldtype.load(val, baseuri, loadingOptions, keys=keys)


save_type = Optional[Union[MutableMapping[str, Any], MutableSequence[Any], int, float, bool, str]]


def parse_errors(error_message: str) -> str:
    """Parse error messages from several loaders into one error message."""
    types = set()
    if not error_message.startswith("Expected"):
        return error_message
    vals = error_message.split("Expected ")
    if len(vals) == 1:
        return error_message
    for val in vals:
        individual_vals = val.split(" ")
        if val == "":
            continue
        if individual_vals[0] == "one":
            individual_vals = val.split("(")[1].split(",")
            for t in individual_vals:
                types.add(t.strip(" ").strip(")\n"))
        else:
            if individual_vals[1] == "<class":
                types.add(individual_vals[2].strip(">").replace("'", ""))
            else:
                types.add(individual_vals[1].replace(",", ""))
    types = set(val for val in types if val != "NoneType")
    if "str" in types:
        types = set(val for val in types if "'" not in val)
    return str(types).replace("{", "(").replace("}", ")").replace("'", "")


def save(
    val: Any,
    top: bool = True,
    base_url: str = "",
    relative_uris: bool = True,
) -> save_type:
    if isinstance(val, Saveable):
        return val.save(top=top, base_url=base_url, relative_uris=relative_uris)
    if isinstance(val, MutableSequence):
        return [save(v, top=False, base_url=base_url, relative_uris=relative_uris) for v in val]
    if isinstance(val, MutableMapping):
        newdict = {}
        for key in val:
            newdict[key] = save(val[key], top=False, base_url=base_url, relative_uris=relative_uris)
        return newdict
    if val is None or isinstance(val, (int, float, bool, str)):
        return val
    raise Exception("Not Saveable: %s" % type(val))


def save_with_metadata(
    val: Any,
    valLoadingOpts: LoadingOptions,
    top: bool = True,
    base_url: str = "",
    relative_uris: bool = True,
) -> save_type:
    """Save and set $namespaces, $schemas, $base and any other metadata fields at the top level."""
    saved_val = save(val, top, base_url, relative_uris)
    newdict: MutableMapping[str, Any] = {}
    if isinstance(saved_val, MutableSequence):
        newdict = {"$graph": saved_val}
    elif isinstance(saved_val, MutableMapping):
        newdict = saved_val

    if valLoadingOpts.namespaces:
        newdict["$namespaces"] = valLoadingOpts.namespaces
    if valLoadingOpts.schemas:
        newdict["$schemas"] = valLoadingOpts.schemas
    if valLoadingOpts.baseuri:
        newdict["$base"] = valLoadingOpts.baseuri
    for k, v in valLoadingOpts.addl_metadata.items():
        if k not in newdict:
            newdict[k] = v

    return newdict


def expand_url(
    url,  # type: str
    base_url,  # type: str
    loadingOptions,  # type: LoadingOptions
    scoped_id=False,  # type: bool
    vocab_term=False,  # type: bool
    scoped_ref=None,  # type: Optional[int]
):
    # type: (...) -> str
    if url in ("@id", "@type"):
        return url

    if vocab_term and url in loadingOptions.vocab:
        return url

    if bool(loadingOptions.vocab) and ":" in url:
        prefix = url.split(":")[0]
        if prefix in loadingOptions.vocab:
            url = loadingOptions.vocab[prefix] + url[len(prefix) + 1 :]

    split = urlsplit(url)

    if (
        (bool(split.scheme) and split.scheme in loadingOptions.fetcher.supported_schemes())
        or url.startswith("$(")
        or url.startswith("${")
    ):
        pass
    elif scoped_id and not bool(split.fragment):
        splitbase = urlsplit(base_url)
        frg = ""
        if bool(splitbase.fragment):
            frg = splitbase.fragment + "/" + split.path
        else:
            frg = split.path
        pt = splitbase.path if splitbase.path != "" else "/"
        url = urlunsplit((splitbase.scheme, splitbase.netloc, pt, splitbase.query, frg))
    elif scoped_ref is not None and not bool(split.fragment):
        splitbase = urlsplit(base_url)
        sp = splitbase.fragment.split("/")
        n = scoped_ref
        while n > 0 and len(sp) > 0:
            sp.pop()
            n -= 1
        sp.append(url)
        url = urlunsplit(
            (
                splitbase.scheme,
                splitbase.netloc,
                splitbase.path,
                splitbase.query,
                "/".join(sp),
            )
        )
    else:
        url = loadingOptions.fetcher.urljoin(base_url, url)

    if vocab_term:
        split = urlsplit(url)
        if bool(split.scheme):
            if url in loadingOptions.rvocab:
                return loadingOptions.rvocab[url]
        else:
            raise ValidationException(f"Term {url!r} not in vocabulary")

    return url


class _Loader:
    def load(self, doc, baseuri, loadingOptions, keys=None, docRoot=None):
        # type: (Any, str, LoadingOptions, Optional[str], Optional[List]) -> Any
        pass


class _AnyLoader(_Loader):
    def load(self, doc, baseuri, loadingOptions, keys=None, docRoot=None):
        # type: (Any, str, LoadingOptions, Optional[str], Optional[List]) -> Any
        if doc is not None:
            return doc
        raise ValidationException("Expected non-null")


class _PrimitiveLoader(_Loader):
    def __init__(self, tp):
        # type: (Union[type, Tuple[Type[str], Type[str]]]) -> None
        self.tp = tp

    def load(self, doc, baseuri, loadingOptions, keys=None, docRoot=None):
        # type: (Any, str, LoadingOptions, Optional[str], Optional[List]) -> Any
        if not isinstance(doc, self.tp):
            raise ValidationException(
                "Expected a {} but got {}".format(self.tp, doc.__class__.__name__)
            )
        return doc

    def __repr__(self):  # type: () -> str
        return str(self.tp)


class _ArrayLoader(_Loader):
    def __init__(self, items):
        # type: (_Loader) -> None
        self.items = items

    def load(self, doc, baseuri, loadingOptions, keys=None, docRoot=None):
        # type: (Any, str, LoadingOptions, Optional[str], Optional[List]) -> Any
        if not isinstance(doc, MutableSequence):
            raise ValidationException(f"Expected a list, was {type(doc)}")
        r = []  # type: List[Any]
        errors = []  # type: List[SchemaSaladException]
        fields = []  # type: List[str]

        append_index = False  # type: bool
        global_doc = copy.copy(line_numbers)  # type: CommentedMap

        if keys is not None:
            
            for key in keys:

                if isinstance(global_doc, CommentedMap):
                    global_doc = global_doc.get(key)
                elif isinstance(global_doc, (CommentedSeq, list)) and isinstance(key, int):
                    if key < len(global_doc):
                        global_doc = global_doc[key]
                    else:
                        global_doc = None
                else:
                    global_doc = None
                    break

        if isinstance(global_doc, (list, CommentedSeq)):
            append_index = True

        for i in range(0, len(doc)):
            try:
                if append_index:
                    lf = load_field(
                        doc[i],
                        _UnionLoader((self, self.items)),
                        baseuri,
                        loadingOptions,
                        keys=keys + [i],
                    )
                else:
                    lf = load_field(
                        doc[i],
                        _UnionLoader((self, self.items)),
                        baseuri,
                        loadingOptions,
                        keys=keys,
                    )
                if isinstance(lf, MutableSequence):
                    r.extend(lf)
                else:
                    r.append(lf)

                if isinstance(doc[i], CommentedMap):
                    if doc[i].get("id") is not None:
                        if doc[i].get("id") in fields:
                            errors.append(
                                ValidationException(
                                    f"Duplicate field '{doc[i].get('id')}'",
                                    SourceLine(doc[i], "id", str),
                                    [],
                                )
                            )
                        else:
                            fields.append(doc[i].get("id"))

            except ValidationException as e:
                errors.append(e.with_sourceline(SourceLine(doc, i, str)))
        if errors:
            raise ValidationException("", None, errors)
        return r

    def __repr__(self):  # type: () -> str
        return f"array<{self.items}>"


class _EnumLoader(_Loader):
    def __init__(self, symbols: Sequence[str], name: str) -> None:
        self.symbols = symbols
        self.name = name

    def load(self, doc, baseuri, loadingOptions, keys=None, docRoot=None):
        # type: (Any, str, LoadingOptions, Optional[str], Optional[List[str]]) -> Any
        if doc in self.symbols:
            return doc
        else:
            raise ValidationException(f"Expected one of {self.symbols}")

    def __repr__(self):  # type: () -> str
        return self.name


class _SecondaryDSLLoader(_Loader):
    def __init__(self, inner):
        # type: (_Loader) -> None
        self.inner = inner

    def load(self, doc, baseuri, loadingOptions, keys=None, docRoot=None):
        # type: (Any, str, LoadingOptions, Optional[str], Optional[List[str]]) -> Any
        r: List[Dict[str, Any]] = []
        if isinstance(doc, MutableSequence):
            for d in doc:
                if isinstance(d, str):
                    if d.endswith("?"):
                        r.append({"pattern": d[:-1], "required": False})
                    else:
                        r.append({"pattern": d})
                elif isinstance(d, dict):
                    new_dict: Dict[str, Any] = {}
                    dict_copy = copy.deepcopy(d)
                    if "pattern" in dict_copy:
                        new_dict["pattern"] = dict_copy.pop("pattern")
                    else:
                        raise ValidationException(
                            f"Missing pattern in secondaryFiles specification entry: {d}"
                        )
                    new_dict["required"] = (
                        dict_copy.pop("required") if "required" in dict_copy else None
                    )

                    if len(dict_copy):
                        raise ValidationException(
                            "Unallowed values in secondaryFiles specification entry: {}".format(
                                dict_copy
                            )
                        )
                    r.append(new_dict)

                else:
                    raise ValidationException(
                        "Expected a string or sequence of (strings or mappings)."
                    )
        elif isinstance(doc, MutableMapping):
            new_dict = {}
            doc_copy = copy.deepcopy(doc)
            if "pattern" in doc_copy:
                new_dict["pattern"] = doc_copy.pop("pattern")
            else:
                raise ValidationException(
                    f"Missing pattern in secondaryFiles specification entry: {doc}"
                )
            new_dict["required"] = doc_copy.pop("required") if "required" in doc_copy else None

            if len(doc_copy):
                raise ValidationException(
                    f"Unallowed values in secondaryFiles specification entry: {doc_copy}"
                )
            r.append(new_dict)

        elif isinstance(doc, str):
            if doc.endswith("?"):
                r.append({"pattern": doc[:-1], "required": False})
            else:
                r.append({"pattern": doc})
        else:
            raise ValidationException("Expected str or sequence of str")
        return self.inner.load(r, baseuri, loadingOptions, docRoot, keys=keys)


class _RecordLoader(_Loader):
    def __init__(self, classtype):
        # type: (Type[Saveable]) -> None
        self.classtype = classtype

    def load(self, doc, baseuri, loadingOptions, keys=None, docRoot=None):
        # type: (Any, str, LoadingOptions, Optional[str], Optional[List]) -> Any
        if not isinstance(doc, MutableMapping):
            raise ValidationException(f"Expected a dict, was {type(doc)}")
        return self.classtype.fromDoc(
            doc, baseuri, loadingOptions, docRoot=docRoot, keys=keys
        )

    def __repr__(self):  # type: () -> str
        return str(self.classtype.__name__)


class _ExpressionLoader(_Loader):
    def __init__(self, items: Type[str]) -> None:
        self.items = items

    def load(self, doc, baseuri, loadingOptions, keys=None, docRoot=None):
        # type: (Any, str, LoadingOptions, Optional[str],Optional[List]) -> Any
        if not isinstance(doc, str):
            raise ValidationException(f"Expected a str, was {type(doc)}")
        return doc


class _UnionLoader(_Loader):
    def __init__(self, alternates: Sequence[_Loader]) -> None:
        self.alternates = alternates

    def load(self, doc, baseuri, loadingOptions, keys=None, docRoot=None):
        # type: (Any, str, LoadingOptions, Optional[str], Optional[List]) -> Any
        errors = []
        temp_doc = copy.copy(line_numbers)
        for t in self.alternates:
            try:
                return t.load(
                    doc, baseuri, loadingOptions, docRoot=docRoot, keys=copy.copy(keys)
                )
            except ValidationException as e:
                if isinstance(doc, (CommentedMap, dict)):
                    if "class" in doc:
                        if doc.get("class") == str(t):
                            errors.append(
                                ValidationException(
                                    f"Object '{baseuri.split('/')[-1]}' is not valid because",
                                    SourceLine(doc, next(iter(doc)), str),
                                    [e],
                                )
                            )
                    else:
                        if "array" in str(t):
                            continue
                        else:
                            id_doc = temp_doc
                            if keys is not None:
                                for key in keys:
                                    if isinstance(id_doc, CommentedMap):
                                        id_doc = id_doc.get(key)
                                    elif isinstance(id_doc, (CommentedSeq, list)) and isinstance(key, int):
                                        if key < len(id_doc):
                                            id_doc = id_doc[key]
                                        else:
                                            id_doc = None
                                    else:
                                        id_doc = None
                                        break
                            if "id" in doc:
                                id = baseuri.split("/")[-1] + "#" + doc.get("id")
                                if "id" in temp_doc:
                                    errors.append(
                                    ValidationException(
                                        f"checking object {id}",
                                        SourceLine(id_doc,"id", str),
                                        [e],
                                    )
                                )
                                else:

                                    errors.append(
                                        ValidationException(
                                            f"checking object {id}",
                                            SourceLine(id_doc, doc.get("id"), str),
                                            [e],
                                        )
                                    )
                            else:
                                if not isinstance(
                                    t, (_PrimitiveLoader)
                                ):  # avoids 'tried <class "NoneType"> was {x}' errors
                                    errors.append(
                                        ValidationException(f"tried {t} but", None, [e])
                                    )
                else:
                    if isinstance(
                        t, (_EnumLoader, _PrimitiveLoader)
                    ):  # avoids "tried <class "CWLType"> but x" and instead returns the values for parsing
                        errors.append(ValidationException("", None, [e]))
                    else:
                        errors.append(ValidationException(f"tried {t} but", None, [e]))

        if isinstance(doc, (CommentedMap, dict)) and "class" in doc:
            if doc.get("class") not in str(self.alternates):
                errors.append(
                    ValidationException(
                        "Field `class` contains undefined reference to "
                        + baseuri
                        + "/"
                        + doc.get("class"),
                        SourceLine(doc, "class", str),
                        [],
                    )
                )
        raise ValidationException("", None, errors, "-")

    def __repr__(self):  # type: () -> str
        return " | ".join(str(a) for a in self.alternates)


class _URILoader(_Loader):
    def __init__(self, inner, scoped_id, vocab_term, scoped_ref):
        # type: (_Loader, bool, bool, Union[int, None]) -> None
        self.inner = inner
        self.scoped_id = scoped_id
        self.vocab_term = vocab_term
        self.scoped_ref = scoped_ref

    def load(self, doc, baseuri, loadingOptions, keys=None, docRoot=None):
        # type: (Any, str, LoadingOptions, Optional[str], Optional[List]) -> Any
        if isinstance(doc, MutableSequence):
            newdoc = []
            for i in doc:
                if isinstance(i, str):
                    newdoc.append(
                        expand_url(
                            i,
                            baseuri,
                            loadingOptions,
                            self.scoped_id,
                            self.vocab_term,
                            self.scoped_ref,
                        )
                    )
                else:
                    newdoc.append(i)
            doc = newdoc
        elif isinstance(doc, str):
            doc = expand_url(
                doc,
                baseuri,
                loadingOptions,
                self.scoped_id,
                self.vocab_term,
                self.scoped_ref,
            )
        if isinstance(doc, str):
            errors = []
            try:
                if not loadingOptions.check_exists(doc):
                    errors.append(
                        ValidationException(f"contains undefined reference to {doc}")
                    )
            except ValidationException as e:
                pass
            if len(errors) > 0:
                raise ValidationException("", None, errors)
        return self.inner.load(doc, baseuri, loadingOptions, keys=keys)


class _TypeDSLLoader(_Loader):
    typeDSLregex = re.compile(r"^([^[?]+)(\[\])?(\?)?$")

    def __init__(self, inner, refScope):
        # type: (_Loader, Union[int, None]) -> None
        self.inner = inner
        self.refScope = refScope

    def resolve(
        self,
        doc,  # type: str
        baseuri,  # type: str
        loadingOptions,  # type: LoadingOptions
    ):
        # type: (...) -> Union[List[Union[Dict[str, str], str]], Dict[str, str], str]
        m = self.typeDSLregex.match(doc)
        if m:
            group1 = m.group(1)
            assert group1 is not None  # nosec
            first = expand_url(group1, baseuri, loadingOptions, False, True, self.refScope)
            second = third = None
            if bool(m.group(2)):
                second = {"type": "array", "items": first}
                # second = CommentedMap((("type", "array"),
                #                       ("items", first)))
                # second.lc.add_kv_line_col("type", lc)
                # second.lc.add_kv_line_col("items", lc)
                # second.lc.filename = filename
            if bool(m.group(3)):
                third = ["null", second or first]
                # third = CommentedSeq(["null", second or first])
                # third.lc.add_kv_line_col(0, lc)
                # third.lc.add_kv_line_col(1, lc)
                # third.lc.filename = filename
            return third or second or first
        return doc

    def load(self, doc, baseuri, loadingOptions, keys=None, docRoot=None):
        # type: (Any, str, LoadingOptions, Optional[str],Optional[List[str]]) -> Any
        if isinstance(doc, MutableSequence):
            r = []  # type: List[Any]
            for d in doc:
                if isinstance(d, str):
                    resolved = self.resolve(d, baseuri, loadingOptions)
                    if isinstance(resolved, MutableSequence):
                        for i in resolved:
                            if i not in r:
                                r.append(i)
                    else:
                        if resolved not in r:
                            r.append(resolved)
                else:
                    r.append(d)
            doc = r
        elif isinstance(doc, str):
            doc = self.resolve(doc, baseuri, loadingOptions)

        return self.inner.load(doc, baseuri, loadingOptions, keys=keys)


class _IdMapLoader(_Loader):
    def __init__(self, inner, mapSubject, mapPredicate):
        # type: (_Loader, str, Union[str, None]) -> None
        self.inner = inner
        self.mapSubject = mapSubject
        self.mapPredicate = mapPredicate

    def load(self, doc, baseuri, loadingOptions, keys=None, docRoot=None):
        # type: (Any, str, LoadingOptions, Optional[str],Optional[List]) -> Any
        if isinstance(doc, MutableMapping):
            r = []  # type: List[Any]
            for k in sorted(doc.keys()):
                val = doc[k]
                if isinstance(val, CommentedMap):
                    v = copy.copy(val)
                    v.lc.data = val.lc.data
                    v.lc.filename = val.lc.filename
                    v[self.mapSubject] = k
                    r.append(v)
                elif isinstance(val, MutableMapping):
                    v2 = copy.copy(val)
                    v2[self.mapSubject] = k
                    r.append(v2)
                else:
                    if self.mapPredicate:
                        v3 = {self.mapPredicate: val}
                        v3[self.mapSubject] = k
                        r.append(v3)
                    else:
                        raise ValidationException("No mapPredicate")
            doc = r
        return self.inner.load(doc, baseuri, loadingOptions, keys=keys)


def _document_load(
    loader: _Loader,
    doc: Union[str, MutableMapping[str, Any], MutableSequence[Any]],
    baseuri: str,
    loadingOptions: LoadingOptions,
    addl_metadata_fields: Optional[MutableSequence[str]] = None,
) -> Tuple[Any, LoadingOptions]:
    if isinstance(doc, str):
        return _document_load_by_url(
            loader,
            loadingOptions.fetcher.urljoin(baseuri, doc),
            loadingOptions,
            addl_metadata_fields=addl_metadata_fields,
        )

    if isinstance(doc, MutableMapping):
        addl_metadata = {}
        if addl_metadata_fields is not None:
            for mf in addl_metadata_fields:
                if mf in doc:
                    addl_metadata[mf] = doc[mf]

        docuri = baseuri
        if "$base" in doc:
            baseuri = doc["$base"]

        loadingOptions = LoadingOptions(
            copyfrom=loadingOptions,
            namespaces=doc.get("$namespaces", None),
            schemas=doc.get("$schemas", None),
            baseuri=doc.get("$base", None),
            addl_metadata=addl_metadata,
        )

        if type(doc) == CommentedMap:
            global line_numbers
            line_numbers = doc

        if "$graph" in doc:
            loadingOptions.idx[baseuri] = (
                loader.load(doc["$graph"], baseuri, loadingOptions),
                loadingOptions,
            )
        else:
            loadingOptions.idx[baseuri] = (
                loader.load(doc, baseuri, loadingOptions, docRoot=baseuri),
                loadingOptions,
            )

        if docuri != baseuri:
            loadingOptions.idx[docuri] = loadingOptions.idx[baseuri]

        return loadingOptions.idx[baseuri]

    if isinstance(doc, MutableSequence):
        loadingOptions.idx[baseuri] = (
            loader.load(doc, baseuri, loadingOptions),
            loadingOptions,
        )
        return loadingOptions.idx[baseuri]

    raise ValidationException(
        "Expected URI string, MutableMapping or MutableSequence, got %s" % type(doc)
    )


def _document_load_by_url(
    loader: _Loader,
    url: str,
    loadingOptions: LoadingOptions,
    addl_metadata_fields: Optional[MutableSequence[str]] = None,
) -> Tuple[Any, LoadingOptions]:
    if url in loadingOptions.idx:
        return loadingOptions.idx[url]

    doc_url, frg = urldefrag(url)

    text = loadingOptions.fetcher.fetch_text(doc_url)
    textIO = StringIO(text)
    textIO.name = str(doc_url)
    yaml = yaml_no_ts()
    result = yaml.load(textIO)
    add_lc_filename(result, doc_url)

    loadingOptions = LoadingOptions(copyfrom=loadingOptions, fileuri=doc_url)

    _document_load(
        loader,
        result,
        doc_url,
        loadingOptions,
        addl_metadata_fields=addl_metadata_fields,
    )

    return loadingOptions.idx[url]


def file_uri(path, split_frag=False):  # type: (str, bool) -> str
    if path.startswith("file://"):
        return path
    if split_frag:
        pathsp = path.split("#", 2)
        frag = "#" + quote(str(pathsp[1])) if len(pathsp) == 2 else ""
        urlpath = pathname2url(str(pathsp[0]))
    else:
        urlpath = pathname2url(path)
        frag = ""
    if urlpath.startswith("//"):
        return f"file:{urlpath}{frag}"
    else:
        return f"file://{urlpath}{frag}"


def prefix_url(url: str, namespaces: Dict[str, str]) -> str:
    """Expand short forms into full URLs using the given namespace dictionary."""
    for k, v in namespaces.items():
        if url.startswith(v):
            return k + ":" + url[len(v) :]
    return url


def save_relative_uri(
    uri: Any,
    base_url: str,
    scoped_id: bool,
    ref_scope: Optional[int],
    relative_uris: bool,
) -> Any:
    """Convert any URI to a relative one, obeying the scoping rules."""
    if isinstance(uri, MutableSequence):
        return [save_relative_uri(u, base_url, scoped_id, ref_scope, relative_uris) for u in uri]
    elif isinstance(uri, str):
        if not relative_uris or uri == base_url:
            return uri
        urisplit = urlsplit(uri)
        basesplit = urlsplit(base_url)
        if urisplit.scheme == basesplit.scheme and urisplit.netloc == basesplit.netloc:
            if urisplit.path != basesplit.path:
                p = os.path.relpath(urisplit.path, os.path.dirname(basesplit.path))
                if urisplit.fragment:
                    p = p + "#" + urisplit.fragment
                return p

            basefrag = basesplit.fragment + "/"
            if ref_scope:
                sp = basefrag.split("/")
                i = 0
                while i < ref_scope:
                    sp.pop()
                    i += 1
                basefrag = "/".join(sp)

            if urisplit.fragment.startswith(basefrag):
                return urisplit.fragment[len(basefrag) :]
            else:
                return urisplit.fragment
        return uri
    else:
        return save(uri, top=False, base_url=base_url, relative_uris=relative_uris)


def shortname(inputid: str) -> str:
    """
    Compute the shortname of a fully qualified identifier.

    See https://w3id.org/cwl/v1.2/SchemaSalad.html#Short_names.
    """
    parsed_id = urlparse(inputid)
    if parsed_id.fragment:
        return parsed_id.fragment.split("/")[-1]
    return parsed_id.path.split("/")[-1]
