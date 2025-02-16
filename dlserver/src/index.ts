/**
 * Welcome to Cloudflare Workers! This is your first worker.
 *
 * - Run `npm run dev` in your terminal to start a development server
 * - Open a browser tab at http://localhost:8787/ to see your worker in action
 * - Run `npm run deploy` to publish your worker
 *
 * Bind resources to your worker in `wrangler.jsonc`. After adding bindings, a type definition for the
 * `Env` object can be regenerated with `npm run cf-typegen`.
 *
 * Learn more at https://developers.cloudflare.com/workers/
 */

import {BlobWriter, Entry, HttpReader, ZipReader, ZipWriter, configure} from '@zip.js/zip.js';

// The "official" URL does not work from Cloudflare servers, so we download from archive.org
// archive.org servers are slow, but we use very heavy caching so it's ok
// const APPLEMUSIC_APK_URL = "https://apps.mzstatic.com/content/android-apple-music-apk/applemusic.apk";

// note the direct link to the APK, not the HTML frontend
const APPLEMUSIC_APK_URL = "https://web.archive.org/web/20231226115856if_/https://apps.mzstatic.com/content/android-apple-music-apk/applemusic.apk";

const LIB_DIR = "lib";
const LIBS = [
    "libstoreservicescore.so",
    "libCoreADI.so",
];


const getCacheKey = (arch: string): string => {
    return `lib-bundle-${arch}`;
}


// Reimplement CompressionStream API to circumvent issue in workerd
// https://github.com/cloudflare/workerd/issues/992
configure({
    useCompressionStream: true,  // MUST be set to TRUE, otherwise files in the resulting zip will be empty
    // @ts-ignore
    CompressionStream: class {
        constructor(format: any) {
            const compressionStream = new CompressionStream(format);
            console.log(compressionStream);
            const writer = compressionStream.writable.getWriter();
            return new TransformStream({
                async transform(chunk) {
                    await writer.write(chunk);
                },
                async flush() {
                    await writer.close();
                }
            });
        }
    }
});


class BundleCreationError extends Error {
    public readonly statusCode: number;

    constructor(message: string, statusCode: number) {
        // Need to pass `options` as the second parameter to install the "cause" property.
        super(message);

        this.statusCode = statusCode;
    }
}


export default {
    async fetch(request, env, _ctx): Promise<Response> {
        const url = new URL(request.url);
        const arch = url.searchParams.get("arch");
        const version = url.searchParams.get("version");

        if (url.pathname === "/apk") return Response.redirect(APPLEMUSIC_APK_URL, 302);
        if (url.pathname !== "/libs") return new Response("Not found", {status: 404});
        if (!arch) return new Response("Architecture missing", {status: 400});

        // Check if bundle exists in cache and return it
        const cachedBundle = await env.bundles.get(getCacheKey(arch), 'stream');
        if (cachedBundle) {
            console.log("Found bundle in cache");
            return new Response(
                cachedBundle,
                {headers: {"Content-Type": "application/zip"}},
            );
        }

        // Create bundle if not exists
        let bundle: Blob;
        try {
            bundle = await createBundle(arch);
        } catch (e) {
            if (e instanceof BundleCreationError) {
                return new Response(e.message, {status: e.statusCode});
            }
            throw e;
        }

        await env.bundles.put(getCacheKey(arch), await bundle.arrayBuffer());
        return new Response(
            bundle,
            {headers: {"Content-Type": "application/zip"}},
        );
    }
} satisfies ExportedHandler<Env>;

async function createBundle(arch: string): Promise<Blob> {
    const apkReader = new ZipReader(new HttpReader(
        APPLEMUSIC_APK_URL, {forceRangeRequests: true},
    ));

    console.log("Created APK reader")

    const wantedLibs = LIBS.map(lib => `${LIB_DIR}/${arch}/${lib}`);
    const libs: Entry[] = [];
    for await (let entry of apkReader.getEntriesGenerator()) {
        if (wantedLibs.includes(entry.filename)) {
            libs.push(entry);
        }

        // Avoid reading more than necessary
        if (libs.length === LIBS.length) break;
    }

    if (libs.length !== LIBS.length) {
        throw new BundleCreationError(`Could not find all libs for ${arch}; is the architecture correct?`, 400)
    }

    console.log("Located required libs");

    // Download libraries from zip as blobs
    const libsData = await Promise.all(libs.map(async lib => {
        const name = lib.filename.split("/").reverse()[0];

        console.log(`Getting ${name}`);
        const data = await lib.getData!(new BlobWriter());
        console.log(`Got ${name}`);

        return {name, data};
    }));
    await apkReader.close();

    // Construct final bundle stream with desired libraries
    const bundleStream = new TransformStream();
    const bundleBlobPromise = new Response(bundleStream.readable).blob();

    // Write to bundle
    const bundleWriter = new ZipWriter(bundleStream, {});
    for (let lib of libsData) {
        console.log(`Writing ${lib.name} to bundle (size: ${lib.data.size})`);
        await bundleWriter.add(lib.name, lib.data.stream());
    }
    await bundleWriter.close();
    const bundleBlob = await bundleBlobPromise;

    console.log(`Crafted final bundle (size: ${bundleBlob.size})`);

    return bundleBlob;
}
