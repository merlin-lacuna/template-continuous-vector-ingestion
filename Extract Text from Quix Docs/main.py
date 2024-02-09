### IMPORT THE STUFF
from quix_docs_parser import quix_docs_extractor
from langchain.text_splitter import RecursiveCharacterTextSplitter
import re
import logging
import uuid
import json

import time
from bs4 import BeautifulSoup, SoupStrainer
from langchain_community.document_loaders import RecursiveUrlLoader, SitemapLoader, DirectoryLoader, BSHTMLLoader
import os

from quixstreams.kafka import Producer
from quixstreams.platforms.quix import QuixKafkaConfigsBuilder, TopicCreationConfigs
from quixstreams.models.serializers import (
    QuixTimeseriesSerializer,
    SerializationContext,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

textchunksize = 4000
textoverlapsize = 200

### USING WEB CRAWLER
# Inspired by: https://github.com/langchain-ai/chat-langchain/blob/master/ingest.py

def metadata_extractor(meta: dict, soup: BeautifulSoup) -> dict:
    title = soup.find("title")
    description = soup.find("meta", attrs={"name": "description"})
    html = soup.find("html")
    return {
        "source": meta["loc"],
        "title": title.get_text() if title else "",
        "description": description.get("content", "") if description else "",
        "language": html.get("lang", "") if html else "",
        **meta,
    }

def load_quix_docs():
    return SitemapLoader(
        "https://quix.io/docs/sitemap.xml",
        filter_urls=["https://quix.io/docs/"],
        parsing_function=quix_docs_extractor,
        default_parser="lxml",
        bs_kwargs={
            "parse_only": SoupStrainer(
                name="div", attrs={"class": "md-content"}
            ),
        },
        meta_function=metadata_extractor,
    ).load()

def load_quix_docs_local():
    html_loader_kwargs = {'open_encoding': 'utf-8'}
    return DirectoryLoader(
        r"C:\My Web Sites\QuixDocs\quix.io\docs",
        glob="**/*.html",
        loader_cls=BSHTMLLoader,
        loader_kwargs=html_loader_kwargs
    ).load()

def simple_extractor(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    return re.sub(r"\n\n+", "\n\n", soup.text).strip()

def ingest_docs():
    docs_from_documentation = load_quix_docs()
    #docs_from_documentation = load_quix_docs_local()
    logger.info(f"Loaded {len(docs_from_documentation)} docs from documentation")
    logger.info("Logging first 5 docs..")
    for d in range(5):
        logger.info(f"Doc {d} is:\n {docs_from_documentation[d]} |")

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=textchunksize,
        chunk_overlap=textoverlapsize)
    docs_transformed = text_splitter.split_documents(
        docs_from_documentation
    )
    logger.info(f"Docs after split {len(docs_transformed)}")
    for d in range(5):
        logger.info(f"SplitDoc {d} is:\n {docs_transformed[d]} |")

    return docs_transformed


quixdocs = ingest_docs()

#### START QUIX STUFF ######
outputtopicname = os.environ["output"]
print(f"Producing to output topic: {outputtopicname}...\n\n")

# For non-"Application.Quix" platform producing, config is a bit manual right now
topic = outputtopicname
cfg_builder = QuixKafkaConfigsBuilder()
cfgs, topics, _ = cfg_builder.get_confluent_client_configs([topic])
topic = topics[0]
cfg_builder.create_topics([TopicCreationConfigs(name=topic)])
serialize = QuixTimeseriesSerializer()

idcounter = 0
with Producer(broker_address=cfgs.pop("bootstrap.servers"), extra_config=cfgs) as producer:
    for doc in quixdocs:
        doctext = re.sub(r'\n+', '\n', doc.page_content)
        doctext = re.sub(r' +', ' ', doctext)

        doc_id = idcounter
        doc_key = f"A{'0'*(10-len(str(doc_id)))}{doc_id}"
        doc_uuid = str(uuid.uuid4())
        value = {
            "Timestamp": time.time_ns(),
            "doc_id": doc_id,
            "doc_uuid": doc_uuid,
            "doc_title": doc.metadata['title'],
            "doc_content": doctext,
            "doc_source": doc.metadata['source'],
        }
        print(f"Producing value: {value}")
        idcounter = idcounter + 1
        producer.produce(
            topic=outputtopicname,
            headers=[("uuid", doc_uuid)],  # a dict is also allowed here
            key=doc_key,
            value=serialize(
                value=value, ctx=SerializationContext(topic=topic, headers=headers)
            ),,  # needs to be a string
        )

print("ingested quix docs")
