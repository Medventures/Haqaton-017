import java.nio.file.*;
import java.util.*;
import java.security.cert.X509Certificate;

/** Изолированные проверки на синтетических сертификатах; не входит в runtime классы релиза. */
public final class VerifierChecks {
    interface Check { void run() throws Exception; }
    static void rejected(Check check) throws Exception {
        try { check.run(); } catch (Exception expected) { return; }
        throw new AssertionError("Invalid signature or status was accepted");
    }
    public static void main(String[] args) throws Exception {
        Path p = Paths.get(args[0]); byte[] doc = Files.readAllBytes(p.resolve("document.bin"));
        var xml = Verifier.xml(Files.readAllBytes(p.resolve("signed.xml")));
        var cms = Verifier.cms(Files.readAllBytes(p.resolve("signed.cms")), doc);
        for (var chain : List.of(xml, cms)) {
            if (!Verifier.identity(chain.get(0), "000000000001").equals("000000000001")) throw new AssertionError();
            Verifier.chain(chain, p.resolve("trust"));
            rejected(() -> Verifier.identity(chain.get(0), "000000000002"));
            rejected(() -> Verifier.chain(chain, Paths.get(args[1])));
        }
        rejected(() -> Verifier.xml(Files.readAllBytes(p.resolve("tampered.xml"))));
        rejected(() -> Verifier.xml(Files.readAllBytes(p.resolve("xxe.xml"))));
        rejected(() -> Verifier.cms(Files.readAllBytes(p.resolve("signed.cms")), new byte[]{1}));
        rejected(() -> Verifier.cms(Files.readAllBytes(p.resolve("tampered.cms")), doc));
        X509Certificate issuer = Verifier.certificate(Files.readAllBytes(p.resolve("issuer.cer")));
        byte[] nonce = Files.readAllBytes(p.resolve("nonce.bin"));
        Verifier.checkOcsp(Files.readAllBytes(p.resolve("good.ocsp")), nonce, cms.get(0), issuer);
        rejected(() -> Verifier.checkOcsp(Files.readAllBytes(p.resolve("good.ocsp")), new byte[24], cms.get(0), issuer));
        for (String name : List.of("revoked", "unknown", "stale", "forged", "wrong_certificate"))
            rejected(() -> Verifier.checkOcsp(Files.readAllBytes(p.resolve(name + ".ocsp")), nonce, cms.get(0), issuer));
        System.out.println("PASS: XML/CMS crypto, exact content, IIN, trusted chain, OCSP good/revoked/unknown/stale/forged/nonce/serial");
    }
}
