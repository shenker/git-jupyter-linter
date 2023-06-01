import sys
import traceback

MAX_PACKET_CONTENT_SIZE = 65515  # 65516-1 so that we can add a newline
FILTER_WELCOME = "git-filter-client"
FILTER_VERSION_STR = "version=2"


def read_pktline(input):
    size_bytes = input.buffer.read(4)
    if len(size_bytes) != 4:
        raise EOFError
    size = int(size_bytes, 16)
    if size == 0:
        return None
    else:
        contents_bytes = input.buffer.read(size - 4)
        if len(contents_bytes) != size - 4:
            raise EOFError(
                f"expecting {size - 4} bytes, read {len(contents_bytes)} bytes"
            )
        return contents_bytes


def read_pktline_flush(input):
    data = read_pktline(input)
    if data is not None:
        raise Exception(f"expecting git flush packet, instead got '{data}'")


def parse_text(data):
    if data is not None:
        return data.decode().rstrip("\n")
    else:
        return None


def parse_kv(text):
    if text is None:
        return None
    idx = text.find("=")
    if idx == -1:
        raise Exception(f"expecting '=' in key-value expression '{text}'")
    key = text[:idx]
    value = text[idx + 1 :]
    return key, value


def expect_kv(text, key):
    parsed_key, value = parse_kv(text)
    if parsed_key != key:
        raise Exception(f"expecting key '{key}', instead got '{parsed_key}'")
    return value


def parse_kvs(input):
    d = {}
    while (data := read_pktline(input)) is not None:
        key, value = parse_kv(parse_text(data))
        d[key] = value
    return d


def read_pktline_text(input):
    return parse_text(read_pktline(input))


def format_pktline(data=None):
    if data is None:
        return b"0000"
    else:
        # add 1 to length for LF
        return b"%04x%b\n" % (len(data) + 5, data)


def write_pktline(output, data=None):
    output.buffer.write(format_pktline(data))
    output.buffer.flush()


def chunk(s, size):
    return (s[i : i + size] for i in range(0, len(s), size))


# SEE: https://github.com/git/git/blob/master/contrib/long-running-filter/example.pl
# SEE: https://git-scm.com/docs/gitattributes
# SEE: https://git-scm.com/docs/long-running-process-protocol
# SEE: https://github.com/jelmer/dulwich/blob/master/dulwich/protocol.py
# SEE: https://benhoyt.com/writings/pygit/
def start_filter_server(input, output, filters, error_file=sys.stderr):
    welcome = read_pktline_text(input)
    if welcome != FILTER_WELCOME:
        raise Exception(
            f"expecting git welcome message '{FILTER_WELCOME}', got '{welcome}'"
        )
    version_str = read_pktline_text(input)
    if version_str != FILTER_VERSION_STR:
        raise Exception(
            f"expecting git long-running process protocol version '{FILTER_VERSION_STR}', got '{version_str}'"
        )
    read_pktline_flush(input)
    write_pktline(output, b"git-filter-server")
    write_pktline(output, FILTER_VERSION_STR.encode())
    write_pktline(output)
    client_capabilities = []
    while data := read_pktline(input):
        client_capabilities.append(expect_kv(parse_text(data), "capability"))
    for capability in filters.keys():
        write_pktline(output, f"capability={capability}".encode())
    write_pktline(output)
    while True:
        try:
            meta = parse_kvs(input)
            lines = []
            while line := read_pktline_text(input):
                lines.append(line)
            content = "".join(lines)
            try:
                command = meta["command"]
                filter_func = filters[command]
                filtered_content = filter_func(content, meta)
                if filtered_content is None:
                    filtered_content = content
            except:
                print(traceback.format_exc(), file=error_file, flush=True)
                write_pktline(output, b"status=error")
                write_pktline(output)
            else:
                write_pktline(output, b"status=success")
                write_pktline(output)
                for packet in chunk(filtered_content.encode(), MAX_PACKET_CONTENT_SIZE):
                    write_pktline(output, packet)
                write_pktline(output)
                write_pktline(output)
        except EOFError:
            break
