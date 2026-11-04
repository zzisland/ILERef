# Third-party notices and source provenance

This package retains upstream attribution and license text. No new blanket license is assigned to the research framework. The Apache licenses below do not license the entire framework or historical corpus.

| Material | Included license / source |
|---|---|
| Apache Commons CLI, revision 8d56926d951f1193d438c328bcdb01ddb48aac01 | Original archive includes LICENSE.txt, NOTICE.txt and source headers; duplicate notices in docs/licenses/cli/ |
| Historical Apache Commons Compress records | docs/licenses/compress/ (original Apache-2.0 LICENSE/NOTICE) |
| Historical jsoup records | docs/licenses/jsoup/LICENSE |
| Historical JFreeChart records | docs/licenses/jfreechart/licence-LGPL.txt |
| Modified Test Smell Detector / tsDetect | tools/detector/source/LICENSE (original GPL v3 text), source snapshot, original README and POM; docs/licenses/detector/LICENSE |
| Embedded detector dependencies | Unmodified META-INF LICENSE/NOTICE from the supplied JAR in docs/licenses/detector-embedded/; the NOTICE names Apache Commons Lang and Spring material |
| Java 8 grammar and ANTLR-generated Java source | Original BSD notice: Terence Parr and Sam Harwell, copyright 2014; framework/src/main/java/antlr/Java8.g4 and docs/licenses/antlr/LICENSE |

The fixed detector JAR SHA-256 is `7d89be5e7aab461723bd4b7057a9daf0e3e0595862cf684ca667b8f451b0f3df`. Its bytes match the binary in the source project from which the accompanying detector source snapshot was copied. A source rebuild has not been verified; see tools/detector/provenance.json. The detector is a modified research version, not a claim of an unmodified upstream release. Upstream attribution is retained in its README and source files.

The Java frontend includes the Java 8 grammar and ANTLR 4.7 generated sources. Generated-file path comments and line endings are normalized; the original copyright notices are retained. Source hashes and these changes are recorded in examples/ar_cli/supplemental_sources.json.

The ILERef framework, executable contracts, prompts and RQ records are research materials. No framework-wide license is specified. Third-party licenses apply to their respective materials. Historical availability and exclusion indexes describe the supplied evidence corpus.

Public Python and Maven dependencies retain their own licenses.
