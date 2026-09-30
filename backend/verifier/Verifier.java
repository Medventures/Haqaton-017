import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.security.cert.*;
import java.util.*;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import kz.gov.pki.kalkan.jce.provider.KalkanProvider;
import kz.gov.pki.kalkan.xmldsig.KncaXS;
import org.apache.xml.security.signature.XMLSignature;
import org.bouncycastle.asn1.*;
import org.bouncycastle.asn1.cms.CMSObjectIdentifiers;
import org.bouncycastle.asn1.ocsp.OCSPObjectIdentifiers;
import org.bouncycastle.asn1.x500.style.BCStyle;
import org.bouncycastle.asn1.x509.*;
import org.bouncycastle.cert.*;
import org.bouncycastle.cert.jcajce.*;
import org.bouncycastle.cert.ocsp.*;
import org.bouncycastle.cms.*;
import org.bouncycastle.operator.*;
import org.bouncycastle.operator.jcajce.*;
import org.w3c.dom.*;

/** Серверная проверка НУЦ по схеме doctorCabinet. Ввод только через stdin, без журналирования ПД. */
public final class Verifier {
    static final Provider PROVIDER = new KalkanProvider();
    static final String DS = "http://www.w3.org/2000/09/xmldsig#";
    static final String GOST = "urn:ietf:params:xml:ns:pkigovkz:xmlsec:algorithms:";
    static final String INDIVIDUAL = "1.2.398.3.3.4.1.1";
    static { Security.addProvider(PROVIDER); KncaXS.loadXMLSecurity(); }
    static final class Rejected extends Exception {
        final String code;
        Rejected(String code) { super(code); this.code = code; }
    }
    static void require(boolean value, String code) throws Rejected { if (!value) throw new Rejected(code); }
    static byte[] decode(String value) { return Base64.getDecoder().decode(value); }
    static X509Certificate certificate(byte[] bytes) throws Exception {
        return (X509Certificate) CertificateFactory.getInstance("X.509").generateCertificate(new ByteArrayInputStream(bytes));
    }
    static List<Element> elements(Node parent) {
        List<Element> result = new ArrayList<>();
        for (Node n = parent.getFirstChild(); n != null; n = n.getNextSibling()) if (n instanceof Element) result.add((Element)n);
        return result;
    }
    static Document parse(byte[] xml) throws Exception {
        DocumentBuilderFactory f = DocumentBuilderFactory.newInstance();
        f.setNamespaceAware(true); f.setXIncludeAware(false); f.setExpandEntityReferences(false);
        f.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
        f.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        f.setFeature("http://xml.org/sax/features/external-general-entities", false);
        f.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
        f.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, ""); f.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
        f.setAttribute("http://www.oracle.com/xml/jaxp/properties/maxElementDepth", "32");
        return f.newDocumentBuilder().parse(new ByteArrayInputStream(xml));
    }
    static boolean canonical(String uri) {
        return Set.of("http://www.w3.org/TR/2001/REC-xml-c14n-20010315", "http://www.w3.org/TR/2001/REC-xml-c14n-20010315#WithComments",
            "http://www.w3.org/2001/10/xml-exc-c14n#", "http://www.w3.org/2001/10/xml-exc-c14n#WithComments").contains(uri);
    }
    static List<X509Certificate> xml(byte[] value) throws Exception {
        Document doc = parse(value);
        NodeList signatures = doc.getElementsByTagNameNS(DS, "Signature");
        require(signatures.getLength() == 1, "signature_invalid");
        Element element = (Element)signatures.item(0);
        require(element.getParentNode() == doc.getDocumentElement(), "signature_invalid");
        XMLSignature signature = new XMLSignature(element, "", true);
        var info = signature.getSignedInfo();
        require(info.getLength() == 1 && signature.getObjectLength() == 0 && canonical(info.getCanonicalizationMethodURI()), "signature_invalid");
        var ref = info.item(0); var transforms = ref.getTransforms();
        require("".equals(ref.getURI()) && transforms != null && transforms.getLength() >= 1 && transforms.getLength() <= 2
            && (DS + "enveloped-signature").equals(transforms.item(0).getURI())
            && (transforms.getLength() == 1 || canonical(transforms.item(1).getURI())), "signature_invalid");
        for (String name : List.of("Transform", "CanonicalizationMethod", "SignatureMethod", "DigestMethod")) {
            NodeList nodes = element.getElementsByTagNameNS(DS, name);
            for (int i = 0; i < nodes.getLength(); i++) require(elements(nodes.item(i)).isEmpty(), "signature_invalid");
        }
        String alg = info.getSignatureMethodURI(), digest = ref.getMessageDigestAlgorithm().getAlgorithmURI();
        require((alg.equals("http://www.w3.org/2001/04/xmldsig-more#rsa-sha256") && digest.equals("http://www.w3.org/2001/04/xmlenc#sha256"))
            || (alg.equals(GOST + "gostr34102015-gostr34112015-512") && digest.equals(GOST + "gostr34112015-512")), "signature_invalid");
        require(signature.getKeyInfo() != null, "signature_invalid");
        List<X509Certificate> chain = new ArrayList<>();
        for (Element data : elements(signature.getKeyInfo().getElement())) {
            require(DS.equals(data.getNamespaceURI()) && "X509Data".equals(data.getLocalName()), "signature_invalid");
            for (Element cert : elements(data)) {
                require(DS.equals(cert.getNamespaceURI()) && "X509Certificate".equals(cert.getLocalName()) && elements(cert).isEmpty(), "signature_invalid");
                chain.add(certificate(decode(cert.getTextContent().replaceAll("\\s", ""))));
            }
        }
        require(chain.size() >= 1 && chain.size() <= 8, "signature_invalid");
        require(signature.checkSignatureValue(chain.get(0)), "signature_invalid");
        return chain;
    }
    @SuppressWarnings("unchecked")
    static List<X509Certificate> cms(byte[] encoded, byte[] expected) throws Exception {
        CMSSignedData cms = new CMSSignedData(encoded);
        require(CMSObjectIdentifiers.data.getId().equals(cms.getSignedContentTypeOID()) && cms.getSignedContent() != null, "signature_invalid");
        require(cms.getSignedContent().getContent() instanceof byte[] && MessageDigest.isEqual(expected, (byte[])cms.getSignedContent().getContent()), "document_mismatch");
        Collection<SignerInformation> signers = cms.getSignerInfos().getSigners();
        require(signers.size() == 1, "signature_invalid");
        SignerInformation signer = signers.iterator().next();
        Collection<X509CertificateHolder> all = cms.getCertificates().getMatches(null);
        Collection<X509CertificateHolder> matches = cms.getCertificates().getMatches(signer.getSID());
        require(all.size() >= 1 && all.size() <= 8 && matches.size() == 1, "signature_invalid");
        X509CertificateHolder holder = matches.iterator().next();
        X509Certificate leaf = certificate(holder.getEncoded());
        String d = signer.getDigestAlgOID(), s = signer.getEncryptionAlgOID();
        String algorithm;
        if (d.equals("1.2.398.3.10.1.3.3") && s.equals("1.2.398.3.10.1.1.2.3.2")) algorithm = s;
        else if (d.equals("1.2.398.3.10.1.3.2") && s.equals("1.2.398.3.10.1.1.2.3.1")) algorithm = s;
        else if (d.equals("2.16.840.1.101.3.4.2.1") && Set.of("1.2.840.113549.1.1.1", "1.2.840.113549.1.1.11").contains(s)) algorithm = "SHA256withRSA";
        else if (d.equals("2.16.840.1.101.3.4.2.2") && Set.of("1.2.840.113549.1.1.1", "1.2.840.113549.1.1.12").contains(s)) algorithm = "SHA384withRSA";
        else if (d.equals("2.16.840.1.101.3.4.2.3") && Set.of("1.2.840.113549.1.1.1", "1.2.840.113549.1.1.13").contains(s)) algorithm = "SHA512withRSA";
        else throw new Rejected("signature_invalid");
        ContentVerifierProvider signatures = new ContentVerifierProvider() {
            public boolean hasAssociatedCertificate() { return true; }
            public X509CertificateHolder getAssociatedCertificate() { return holder; }
            public ContentVerifier get(AlgorithmIdentifier id) throws OperatorCreationException {
                try {
                    Signature engine = Signature.getInstance(algorithm, PROVIDER); engine.initVerify(leaf.getPublicKey());
                    ByteArrayOutputStream out = new ByteArrayOutputStream();
                    return new ContentVerifier() {
                        public AlgorithmIdentifier getAlgorithmIdentifier() { return id; }
                        public OutputStream getOutputStream() { return out; }
                        public boolean verify(byte[] bytes) {
                            try { engine.update(out.toByteArray()); return engine.verify(bytes); } catch (GeneralSecurityException e) { return false; }
                        }
                    };
                } catch (GeneralSecurityException e) { throw new OperatorCreationException("signature_invalid"); }
            }
        };
        DigestCalculatorProvider digests = id -> {
            try {
                if (!id.getAlgorithm().getId().equals(d)) throw new GeneralSecurityException();
                MessageDigest digest = MessageDigest.getInstance(d, PROVIDER); ByteArrayOutputStream out = new ByteArrayOutputStream();
                return new DigestCalculator() {
                    public AlgorithmIdentifier getAlgorithmIdentifier() { return id; }
                    public OutputStream getOutputStream() { return out; }
                    public byte[] getDigest() { return digest.digest(out.toByteArray()); }
                };
            } catch (GeneralSecurityException e) { throw new OperatorCreationException("signature_invalid"); }
        };
        require(signer.verify(new SignerInformationVerifier((a,b) -> s, name -> new AlgorithmIdentifier(new ASN1ObjectIdentifier(name)), signatures, digests)), "signature_invalid");
        List<X509Certificate> chain = new ArrayList<>(); chain.add(leaf);
        for (X509CertificateHolder c : all) if (!c.equals(holder)) chain.add(certificate(c.getEncoded()));
        return chain;
    }
    static String identity(X509Certificate leaf, String expected) throws Exception {
        leaf.checkValidity();
        if (leaf.getPublicKey() instanceof java.security.interfaces.RSAPublicKey)
            require(((java.security.interfaces.RSAPublicKey)leaf.getPublicKey()).getModulus().bitLength() >= 2048, "signature_invalid");
        boolean[] usage = leaf.getKeyUsage(); List<String> eku = leaf.getExtendedKeyUsage();
        require(leaf.getBasicConstraints() < 0 && !leaf.hasUnsupportedCriticalExtension() && eku != null && eku.contains(INDIVIDUAL)
            && usage != null && (usage[0] || (usage.length > 1 && usage[1])), "certificate_invalid");
        var serials = new JcaX509CertificateHolder(leaf).getSubject().getRDNs(BCStyle.SERIALNUMBER);
        require(serials.length == 1 && serials[0].size() == 1, "iin_mismatch");
        String serial = serials[0].getFirst().getValue().toString();
        require(serial.matches("IIN[0-9]{12}") && (expected.isEmpty() || serial.equals("IIN" + expected)), "iin_mismatch");
        return serial.substring(3);
    }
    static X509Certificate chain(List<X509Certificate> chain, Path trust) throws Exception {
        Set<TrustAnchor> anchors = new HashSet<>();
        for (String name : List.of("root_rsa_2020.cer", "root_gost_2022.cer")) anchors.add(new TrustAnchor(certificate(Files.readAllBytes(trust.resolve(name))), null));
        List<X509Certificate> candidates = new ArrayList<>(chain);
        for (String name : List.of("nca_rsa_2022.cer", "nca_gost_2022.cer")) candidates.add(certificate(Files.readAllBytes(trust.resolve(name))));
        X509CertSelector selector = new X509CertSelector(); selector.setCertificate(chain.get(0));
        PKIXBuilderParameters params = new PKIXBuilderParameters(anchors, selector);
        params.setRevocationEnabled(false); params.setDate(new Date()); params.setMaxPathLength(3);
        params.addCertStore(CertStore.getInstance("Collection", new CollectionCertStoreParameters(candidates)));
        PKIXCertPathBuilderResult result;
        try { result = (PKIXCertPathBuilderResult)CertPathBuilder.getInstance("PKIX").build(params); }
        catch (GeneralSecurityException e) { throw new Rejected("chain_invalid"); }
        for (java.security.cert.Certificate cert : result.getCertPath().getCertificates()) ((X509Certificate)cert).checkValidity();
        result.getTrustAnchor().getTrustedCert().checkValidity();
        List<? extends java.security.cert.Certificate> path = result.getCertPath().getCertificates();
        return path.size() > 1 ? (X509Certificate)path.get(1) : result.getTrustAnchor().getTrustedCert();
    }
    static void ocsp(X509Certificate leaf, X509Certificate issuer) throws Exception {
        var digests = new JcaDigestCalculatorProviderBuilder().build();
        CertificateID id = new CertificateID(digests.get(CertificateID.HASH_SHA1), new JcaX509CertificateHolder(issuer), leaf.getSerialNumber());
        byte[] nonce = new byte[24]; new SecureRandom().nextBytes(nonce);
        ExtensionsGenerator ext = new ExtensionsGenerator();
        ext.addExtension(OCSPObjectIdentifiers.id_pkix_ocsp_nonce, false, new DEROctetString(nonce));
        OCSPReqBuilder builder = new OCSPReqBuilder(); builder.addRequest(id); builder.setRequestExtensions(ext.generate());
        byte[] body = builder.build().getEncoded();
        HttpURLConnection connection = (HttpURLConnection)new URL("http://ocsp.pki.gov.kz").openConnection();
        try {
            connection.setInstanceFollowRedirects(false); connection.setConnectTimeout(8000); connection.setReadTimeout(12000);
            connection.setRequestMethod("POST"); connection.setDoOutput(true); connection.setFixedLengthStreamingMode(body.length);
            connection.setRequestProperty("Content-Type", "application/ocsp-request"); connection.setRequestProperty("Accept", "application/ocsp-response");
            try (OutputStream out = connection.getOutputStream()) { out.write(body); }
            require(connection.getResponseCode() == 200, "ocsp_unavailable");
            byte[] raw; try (InputStream input = connection.getInputStream()) { raw = input.readNBytes(1_048_577); }
            require(raw.length <= 1_048_576, "ocsp_unavailable");
            checkOcsp(raw, nonce, leaf, issuer);
        } catch (IOException e) { throw new Rejected("ocsp_unavailable"); }
        finally { connection.disconnect(); }
    }
    static void checkOcsp(byte[] raw, byte[] nonce, X509Certificate leaf, X509Certificate issuer) throws Exception {
        OCSPResp response = new OCSPResp(raw);
        require(response.getStatus() == OCSPResp.SUCCESSFUL && response.getResponseObject() instanceof BasicOCSPResp, "ocsp_unavailable");
        BasicOCSPResp basic = (BasicOCSPResp)response.getResponseObject();
        var extension = basic.getExtension(OCSPObjectIdentifiers.id_pkix_ocsp_nonce);
        require(extension != null, "ocsp_invalid");
        byte[] encoded = extension.getExtnValue().getOctets(), actual;
        try { actual = ASN1OctetString.getInstance(encoded).getOctets(); } catch (IllegalArgumentException e) { actual = encoded; }
        require(MessageDigest.isEqual(nonce, actual), "ocsp_invalid");
        List<X509Certificate> candidates = new ArrayList<>();
        for (X509CertificateHolder c : basic.getCerts()) candidates.add(certificate(c.getEncoded()));
        candidates.add(issuer);
        boolean trusted = false;
        for (X509Certificate c : candidates) {
            try {
                if (!basic.isSignatureValid(new JcaContentVerifierProviderBuilder().setProvider(PROVIDER).build(c.getPublicKey()))) continue;
                c.checkValidity();
                if (!MessageDigest.isEqual(c.getEncoded(), issuer.getEncoded())) {
                    if (!c.getIssuerX500Principal().equals(issuer.getSubjectX500Principal()) || c.getExtendedKeyUsage() == null
                        || !c.getExtendedKeyUsage().contains(KeyPurposeId.id_kp_OCSPSigning.getId())) continue;
                    c.verify(issuer.getPublicKey(), PROVIDER);
                }
                trusted = true; break;
            } catch (Exception e) { /* Следующий кандидат; неподтверждённому ответу не доверяем. */ }
        }
        require(trusted, "ocsp_invalid");
        var digests = new JcaDigestCalculatorProviderBuilder().build();
        List<SingleResp> matches = new ArrayList<>();
        for (SingleResp r : basic.getResponses()) if (r.getCertID().getSerialNumber().equals(leaf.getSerialNumber())
            && r.getCertID().matchesIssuer(new JcaX509CertificateHolder(issuer), digests)) matches.add(r);
        require(matches.size() == 1, "ocsp_invalid");
        SingleResp r = matches.get(0); long now = System.currentTimeMillis(), skew = 300_000;
        require(r.getThisUpdate().getTime() <= now + skew && (r.getNextUpdate() != null
            ? r.getNextUpdate().getTime() >= now - skew : r.getThisUpdate().getTime() >= now - 900_000), "ocsp_stale");
        require(r.getCertStatus() == CertificateStatus.GOOD, r.getCertStatus() instanceof RevokedStatus ? "certificate_revoked" : "ocsp_unknown");
    }
    public static void main(String[] args) {
        try {
            if (args.length == 2 && args[0].equals("--self-test")) {
                for (String n : List.of("root_rsa_2020.cer", "root_gost_2022.cer", "nca_rsa_2022.cer", "nca_gost_2022.cer"))
                    certificate(Files.readAllBytes(Paths.get(args[1], n))).checkValidity();
                Signature.getInstance("1.2.398.3.10.1.1.2.3.2", PROVIDER);
                MessageDigest.getInstance("1.2.398.3.10.1.3.3", PROVIDER);
                System.out.print("READY"); return;
            }
            byte[] input = System.in.readNBytes(5_000_001); require(input.length <= 5_000_000, "signature_invalid");
            String[] values = new String(input, StandardCharsets.US_ASCII).split("\n", -1);
            require(values.length == 4 && args.length == 1, "signature_invalid");
            byte[] signature = decode(values[1]), content = decode(values[2]);
            require(signature.length <= 1_500_000 && content.length <= 1_500_000, "signature_invalid");
            List<X509Certificate> certs;
            if (values[0].equals("xml")) certs = xml(signature);
            else if (values[0].equals("cms")) certs = cms(signature, content);
            else throw new Rejected("signature_invalid");
            String iin = identity(certs.get(0), values[3]);
            X509Certificate issuer = chain(certs, Paths.get(args[0]));
            ocsp(certs.get(0), issuer);
            String fingerprint = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(certs.get(0).getEncoded()));
            System.out.print("OK\n" + iin + "\n" + fingerprint);
        } catch (Rejected e) { System.out.print("ERROR\n" + e.code); System.exit(2); }
        catch (CertificateExpiredException | CertificateNotYetValidException e) { System.out.print("ERROR\ncertificate_expired"); System.exit(2); }
        catch (Throwable e) { System.out.print("ERROR\nsignature_invalid"); System.exit(2); }
    }
}
